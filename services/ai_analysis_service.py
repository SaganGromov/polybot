"""
AI Analysis Service

Orchestrates the AI analysis workflow:
1. Checks cache for existing analysis
2. Gathers context from multiple sources
3. Delegates to AI provider (if not cached)
4. Caches result and tracks API usage
5. Logs and returns result

This service acts as the application layer, coordinating between
the domain interfaces and the infrastructure adapters.
"""

import logging
import json
import os
from typing import Optional
from polybot.core.interfaces import AIAnalysisProvider, ExchangeProvider
from polybot.core.models import TradeAnalysis, MarketMetadata, MarketDepth
from polybot.core.events import TradeEvent

logger = logging.getLogger(__name__)

# Cache and state file paths
CACHE_FILE = "polybot/config/ai_analysis_cache.json"
STATE_FILE = "polybot/config/ai_state.json"


class AIAnalysisService:
    """
    High-level service for AI-powered trade analysis.
    
    Features:
    - Caches analysis results per token_id to avoid duplicate API calls
    - Tracks API request count with configurable limit
    - Automatically disables AI when limit is reached
    """
    
    def __init__(
        self, 
        analyzer: AIAnalysisProvider, 
        exchange: ExchangeProvider,
        max_requests: int = 100
    ):
        """
        Initialize the AI analysis service.
        
        Args:
            analyzer: The AI analysis provider (Gemini, Mock, etc.)
            exchange: Exchange provider for fetching market data
            max_requests: Maximum API requests allowed (0 = unlimited)
        """
        self.analyzer = analyzer
        self.exchange = exchange
        self.max_requests = max_requests
        
        # Load cache and state
        self._cache: dict = {}
        self._request_count: int = 0
        self._load_cache()
        self._load_state()
        
        logger.info(f"🤖 AIAnalysisService initialized (requests: {self._request_count}/{self.max_requests if self.max_requests > 0 else '∞'}, cached: {len(self._cache)})")
    
    def _load_cache(self):
        """Load analysis cache from JSON file."""
        try:
            if os.path.exists(CACHE_FILE):
                with open(CACHE_FILE, 'r') as f:
                    self._cache = json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load AI cache: {e}")
            self._cache = {}
    
    def _save_cache(self):
        """Save analysis cache to JSON file."""
        try:
            os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
            with open(CACHE_FILE, 'w') as f:
                json.dump(self._cache, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save AI cache: {e}")
    
    def _load_state(self):
        """Load API request count from state file."""
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, 'r') as f:
                    state = json.load(f)
                    self._request_count = state.get("request_count", 0)
        except Exception as e:
            logger.warning(f"Failed to load AI state: {e}")
            self._request_count = 0
    
    def _save_state(self):
        """Save API request count to state file."""
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            with open(STATE_FILE, 'w') as f:
                json.dump({"request_count": self._request_count}, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save AI state: {e}")
    
    def update_max_requests(self, max_requests: int):
        """Update the maximum API request limit."""
        self.max_requests = max_requests
        logger.info(f"🤖 AI request limit updated: {self._request_count}/{max_requests if max_requests > 0 else '∞'}")
    
    def is_limit_reached(self) -> bool:
        """Check if API request limit has been reached."""
        if self.max_requests <= 0:
            return False  # Unlimited
        return self._request_count >= self.max_requests
    
    def get_cached_analysis(self, token_id: str) -> Optional[TradeAnalysis]:
        """Get cached analysis for a token if it exists."""
        if token_id in self._cache:
            cached = self._cache[token_id]
            try:
                return TradeAnalysis(**cached)
            except Exception:
                return None
        return None
    
    async def should_execute_trade(
        self,
        token_id: str,
        trade_event: TradeEvent,
        market_metadata: Optional[MarketMetadata] = None,
        market_depth: Optional[MarketDepth] = None
    ) -> tuple[bool, TradeAnalysis]:
        """
        Main entry point for trade analysis.
        
        Checks cache first, then calls AI if not cached.
        Respects API request limits.
        """
        # Check cache first
        cached = self.get_cached_analysis(token_id)
        if cached:
            logger.info(f"🤖 Using cached analysis for token (saved API call)")
            self._log_analysis(trade_event, cached, from_cache=True)
            return cached.should_trade, cached
        
        # Check if we've hit the API limit
        if self.is_limit_reached():
            logger.warning(f"🤖 API limit reached ({self._request_count}/{self.max_requests}). Defaulting to APPROVE.")
            fallback = TradeAnalysis(
                should_trade=True,
                confidence=0.0,
                justification=f"API limit reached ({self._request_count} requests). Defaulting to allow trade.",
                risk_factors=["AI analysis skipped - API limit"],
                opportunity_factors=[]
            )
            return True, fallback
        
        try:
            # Fetch market data if not provided
            if market_metadata is None:
                market_metadata = await self.exchange.get_market_metadata(token_id)
            
            if market_depth is None:
                market_depth = await self.exchange.get_order_book(token_id)
            
            # Build context from trade event and additional data
            context = self._build_context(trade_event)
            
            # Delegate to AI provider
            analysis = await self.analyzer.analyze_trade(
                token_id=token_id,
                market_metadata=market_metadata,
                market_depth=market_depth,
                context=context
            )
            
            # Increment request counter and save
            self._request_count += 1
            self._save_state()
            
            # Cache the result
            self._cache[token_id] = analysis.model_dump()
            self._save_cache()
            
            # Log the analysis result
            self._log_analysis(trade_event, analysis)
            
            logger.info(f"   📊 API requests: {self._request_count}/{self.max_requests if self.max_requests > 0 else '∞'}")
            
            return analysis.should_trade, analysis
            
        except Exception as e:
            logger.error(f"AI analysis failed: {e}")
            # On failure, default to allowing the trade
            fallback = TradeAnalysis(
                should_trade=True,
                confidence=0.0,
                justification=f"AI analysis failed ({e}). Defaulting to allow trade.",
                risk_factors=["AI analysis error"],
                opportunity_factors=[]
            )
            return True, fallback
    
    def _build_context(self, trade_event: TradeEvent) -> dict:
        """Build context dictionary from trade event and environment."""
        return {
            "whale_name": trade_event.source_wallet_name,
            "whale_address": trade_event.source_wallet_address,
            "whale_trade_size": trade_event.usd_size,
            "outcome": trade_event.outcome,
            "market_slug": trade_event.market_slug,
            "trade_side": trade_event.side.value,
            "timestamp": trade_event.timestamp.isoformat()
        }
    
    def _log_analysis(self, trade_event: TradeEvent, analysis: TradeAnalysis, from_cache: bool = False):
        """Log the AI analysis result."""
        status = "✅ APPROVE" if analysis.should_trade else "❌ REJECT"
        confidence_pct = analysis.confidence * 100
        cache_tag = " (CACHED)" if from_cache else ""
        
        logger.info(f"🤖 AI Analysis: {status} (Confidence: {confidence_pct:.0f}%){cache_tag}")
        logger.info(f"   Market: {trade_event.market_slug}")
        logger.info(f"   Justification: {analysis.justification}")
        
        if analysis.risk_factors:
            logger.info(f"   Risks: {', '.join(analysis.risk_factors)}")
        
        if analysis.opportunity_factors:
            logger.info(f"   Opportunities: {', '.join(analysis.opportunity_factors)}")
        
        if analysis.estimated_resolution_time:
            logger.info(f"   Est. Resolution: {analysis.estimated_resolution_time}")
        
        if analysis.subjectivity_score is not None:
            subj_label = "Objective" if analysis.subjectivity_score < 0.3 else (
                "Moderate" if analysis.subjectivity_score < 0.7 else "Subjective"
            )
            logger.info(f"   Subjectivity: {subj_label} ({analysis.subjectivity_score:.1f})")

