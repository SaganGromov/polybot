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

import asyncio
import logging
import json
import os
from typing import Optional, Dict, Any
from polybot.core.interfaces import AIAnalysisProvider, ExchangeProvider
from polybot.core.models import TradeAnalysis, MarketMetadata, MarketDepth, SportsSelectivityResult, MarketType
from polybot.core.events import TradeEvent
from polybot.services.rate_limiter import AIRateLimiter

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
    - Sports market filtering with AI classification
    - Circuit breaker to prevent cascading failures
    """
    
    def __init__(
        self, 
        analyzer: AIAnalysisProvider, 
        exchange: ExchangeProvider,
        max_requests: int = 100,
        rate_limit_config: Optional[Dict[str, Any]] = None,
        circuit_breaker_threshold: int = 5,
        circuit_breaker_cooldown: int = 60
    ):
        """
        Initialize the AI analysis service.
        
        Args:
            analyzer: The AI analysis provider (Gemini, Mock, etc.)
            exchange: Exchange provider for fetching market data
            max_requests: Maximum API requests allowed (0 = unlimited)
            rate_limit_config: Rate limiting configuration dict with keys:
                - rate_limit_rps: Requests per second (default: 5.0)
                - max_concurrent_ai: Max concurrent requests (default: 10)
                - queue_timeout: Queue timeout in seconds (default: 120)
            circuit_breaker_threshold: Consecutive failures before circuit opens
            circuit_breaker_cooldown: Seconds to wait before retrying after circuit opens
        """
        self.analyzer = analyzer
        self.exchange = exchange
        self.max_requests = max_requests
        
        # Sports filter configuration (set via update_sports_filter_config)
        self.sports_filter_enabled = False
        self.sports_allow_selective = False
        self.sports_max_days_to_resolution = 4.0
        self.sports_min_favorite_odds = 0.70
        
        # Crypto market rules configuration (set via update_crypto_market_config)
        self.crypto_rules_enabled = False
        
        # Circuit breaker state
        self._circuit_breaker_threshold = circuit_breaker_threshold
        self._circuit_breaker_cooldown = circuit_breaker_cooldown
        self._consecutive_failures = 0
        self._circuit_open_until: float = 0.0  # timestamp when circuit can close
        
        # Initialize rate limiter with config
        rl_config = rate_limit_config or {}
        self.rate_limiter = AIRateLimiter(
            requests_per_second=rl_config.get("rate_limit_rps", 5.0),
            max_concurrent=rl_config.get("max_concurrent_ai", 10),
            queue_timeout=rl_config.get("queue_timeout", 120.0)
        )
        
        # Load cache and state
        self._cache: dict = {}
        self._sports_cache: dict = {}  # Cache for sports classification
        self._crypto_cache: dict = {}  # Cache for crypto market classification
        self._request_count: int = 0
        self._load_cache()
        self._load_state()
        
        logger.info(f"🤖 AIAnalysisService initialized (requests: {self._request_count}/{self.max_requests if self.max_requests > 0 else '∞'}, cached: {len(self._cache)}, circuit_breaker: {circuit_breaker_threshold} failures / {circuit_breaker_cooldown}s cooldown)")
    
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
    
    def update_rate_limit_config(
        self,
        rate_limit_rps: Optional[float] = None,
        max_concurrent_ai: Optional[int] = None,
        queue_timeout: Optional[float] = None
    ):
        """Update rate limiter configuration dynamically."""
        self.rate_limiter.update_config(
            requests_per_second=rate_limit_rps,
            max_concurrent=max_concurrent_ai,
            queue_timeout=queue_timeout
        )
    
    def is_limit_reached(self) -> bool:
        """Check if API request limit has been reached."""
        if self.max_requests <= 0:
            return False  # Unlimited
        return self._request_count >= self.max_requests
    
    def _is_circuit_open(self) -> bool:
        """Check if circuit breaker is open (blocking requests)."""
        import time
        if self._circuit_open_until > 0:
            if time.time() < self._circuit_open_until:
                return True
            else:
                # Circuit can close, reset state
                logger.info("🟢 AI circuit breaker closed - resuming normal operation")
                self._circuit_open_until = 0.0
                self._consecutive_failures = 0
        return False
    
    def _record_failure(self):
        """Record an AI failure for circuit breaker."""
        import time
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._circuit_breaker_threshold:
            self._circuit_open_until = time.time() + self._circuit_breaker_cooldown
            logger.warning(f"🔴 AI circuit breaker OPEN - {self._consecutive_failures} consecutive failures. Blocking AI for {self._circuit_breaker_cooldown}s")
    
    def _record_success(self):
        """Record an AI success to reset circuit breaker."""
        if self._consecutive_failures > 0:
            self._consecutive_failures = 0
    
    def update_circuit_breaker_config(self, threshold: int = None, cooldown: int = None):
        """Update circuit breaker configuration."""
        if threshold is not None:
            self._circuit_breaker_threshold = threshold
        if cooldown is not None:
            self._circuit_breaker_cooldown = cooldown
        logger.info(f"⚡ Circuit breaker config updated: threshold={self._circuit_breaker_threshold}, cooldown={self._circuit_breaker_cooldown}s")
    
    def update_sports_filter_config(
        self, 
        enabled: bool, 
        allow_selective: bool = False,
        max_days_to_resolution: float = 4.0,
        min_favorite_odds: float = 0.70
    ):
        """Update sports filter configuration."""
        self.sports_filter_enabled = enabled
        self.sports_allow_selective = allow_selective
        self.sports_max_days_to_resolution = max_days_to_resolution
        self.sports_min_favorite_odds = min_favorite_odds
        
        status = "ENABLED" if enabled else "DISABLED"
        selective_status = f" (selective: max {max_days_to_resolution} days, min {min_favorite_odds:.0%} odds)" if allow_selective else ""
        logger.info(f"🏈 Sports Filter: {status}{selective_status}")
    
    def update_crypto_market_config(self, enabled: bool):
        """Update crypto market rules configuration."""
        self.crypto_rules_enabled = enabled
        status = "ENABLED" if enabled else "DISABLED"
        logger.info(f"₿ Crypto Market Rules: {status}")
    
    async def check_crypto_market(
        self,
        token_id: str,
        market_metadata: MarketMetadata
    ) -> tuple[bool, str]:
        """
        Check if a market is a crypto price prediction bet.
        
        Returns:
            (is_crypto, reason) - True if market is crypto price prediction
        """
        if not self.crypto_rules_enabled:
            return False, "Crypto rules disabled"
        
        # Check cache first
        if token_id in self._crypto_cache:
            cached = self._crypto_cache[token_id]
            return cached["is_crypto"], f"(cached) {cached['reason']}"
        
        # Delegate to analyzer - pure AI classification
        is_crypto, reason = await self.analyzer.is_crypto_price_market(
            market_metadata=market_metadata
        )
        
        # Cache result
        self._crypto_cache[token_id] = {"is_crypto": is_crypto, "reason": reason}
        
        return is_crypto, reason
    
    async def check_sports_filter(
        self,
        token_id: str,
        market_metadata: MarketMetadata
    ) -> tuple[bool, str]:
        """
        Check if a market should be blocked due to sports filter.
        Uses Gemini AI for classification.
        
        If selective mode is enabled, qualifies sports trades that meet criteria.
        
        Returns:
            (should_block, reason) - True if market is sports and should be blocked
        """
        if not self.sports_filter_enabled:
            return False, "Sports filter disabled"
        
        # Check cache first for sports classification
        if token_id in self._sports_cache:
            cached = self._sports_cache[token_id]
            is_sports = cached["is_sports"]
            cached_reason = cached["reason"]
        else:
            # Delegate to analyzer - pure AI classification
            is_sports, cached_reason = await self.analyzer.is_sports_market(
                market_metadata=market_metadata
            )
            # Cache result
            self._sports_cache[token_id] = {"is_sports": is_sports, "reason": cached_reason}
        
        # If not sports, allow
        if not is_sports:
            return False, cached_reason
        
        # It's a sports market - check if selective mode allows it
        if self.sports_allow_selective:
            selectivity_result = await self.analyzer.evaluate_sports_selectivity(
                market_metadata=market_metadata,
                max_days_to_resolution=self.sports_max_days_to_resolution,
                min_favorite_odds=self.sports_min_favorite_odds
            )
            
            if selectivity_result.qualifies:
                logger.info(f"🏆 Sports trade QUALIFIED for selective exception:")
                logger.info(f"   Favorite: {selectivity_result.favorite_entity} @ {selectivity_result.favorite_odds:.0%}")
                if selectivity_result.hours_to_resolution:
                    logger.info(f"   Resolution: {selectivity_result.hours_to_resolution:.1f} hours")
                logger.info(f"   Reason: {selectivity_result.justification}")
                return False, f"Sports trade qualified: {selectivity_result.justification}"
            else:
                logger.info(f"🏈 Sports trade did NOT qualify for exception: {selectivity_result.justification}")
                return True, f"Sports market (not qualified): {selectivity_result.justification}"
        
        # Sports filter enabled, selective mode disabled - block all sports
        return True, cached_reason
    
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
            logger.warning(f"🤖 API limit reached ({self._request_count}/{self.max_requests}). BLOCKING trade for safety.")
            fallback = TradeAnalysis(
                should_trade=False,
                confidence=0.0,
                justification=f"API limit reached ({self._request_count} requests). Blocking trade for safety.",
                risk_factors=["AI analysis skipped - API limit"],
                opportunity_factors=[]
            )
            return False, fallback
        
        # Check circuit breaker
        if self._is_circuit_open():
            logger.warning(f"🔴 AI circuit breaker OPEN - blocking trade for safety")
            fallback = TradeAnalysis(
                should_trade=False,
                confidence=0.0,
                justification="AI circuit breaker open (too many recent failures). Blocking trade for safety.",
                risk_factors=["AI circuit breaker active"],
                opportunity_factors=[]
            )
            return False, fallback
        
        try:
            # Fetch market data if not provided
            if market_metadata is None:
                market_metadata = await self.exchange.get_market_metadata(token_id)
            
            if market_depth is None:
                market_depth = await self.exchange.get_order_book(token_id)
            
            # Build context from trade event and additional data
            context = self._build_context(trade_event)
            
            # Delegate to AI provider with rate limiting
            try:
                async with await self.rate_limiter.acquire():
                    analysis = await self.analyzer.analyze_trade(
                        token_id=token_id,
                        market_metadata=market_metadata,
                        market_depth=market_depth,
                        context=context
                    )
            except asyncio.TimeoutError:
                self._record_failure()
                logger.warning(f"⏳ AI request queued too long, BLOCKING trade for safety")
                fallback = TradeAnalysis(
                    should_trade=False,
                    confidence=0.0,
                    justification="AI request queue timeout. Blocking trade for safety.",
                    risk_factors=["AI analysis skipped - queue timeout"],
                    opportunity_factors=[]
                )
                return False, fallback
            
            # Increment request counter and save
            self._request_count += 1
            self._save_state()
            
            # Cache the result
            self._cache[token_id] = analysis.model_dump()
            self._save_cache()
            
            # Record success for circuit breaker
            self._record_success()
            
            # Log the analysis result
            self._log_analysis(trade_event, analysis)
            
            logger.info(f"   📊 API requests: {self._request_count}/{self.max_requests if self.max_requests > 0 else '∞'}")
            
            return analysis.should_trade, analysis
            
        except Exception as e:
            self._record_failure()
            logger.error(f"AI analysis failed: {e}")
            # On failure, BLOCK the trade for safety
            fallback = TradeAnalysis(
                should_trade=False,
                confidence=0.0,
                justification=f"AI analysis failed ({e}). Blocking trade for safety.",
                risk_factors=["AI analysis error"],
                opportunity_factors=[]
            )
            return False, fallback
    
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

