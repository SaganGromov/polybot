"""
AI Analyzer Adapter - Gemini Implementation

Implements AIAnalysisProvider using Google's Gemini API.
Follows the adapter pattern established in the codebase.
"""

import logging
import json
import asyncio
import httpx
from polybot.core.interfaces import AIAnalysisProvider
from polybot.core.models import TradeAnalysis, MarketMetadata, MarketDepth, SportsSelectivityResult
from polybot.config.settings import settings

logger = logging.getLogger(__name__)


class GeminiAnalyzerAdapter(AIAnalysisProvider):
    """Gemini-based implementation of AI trade analysis."""
    
    def __init__(self):
        api_key = settings.GEMINI_API_KEY
        if api_key:
            self.api_key = api_key.get_secret_value()
        else:
            self.api_key = None
            logger.warning("GEMINI_API_KEY not set - AI analysis will be disabled")
        
        self.model = "gemini-2.0-flash"
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"
    
    def _build_analysis_prompt(
        self,
        token_id: str,
        market_metadata: MarketMetadata,
        market_depth: MarketDepth,
        context: dict
    ) -> str:
        """Build a comprehensive prompt for trade analysis."""
        
        from datetime import datetime
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M UTC")
        
        # Format order book summary
        best_bid = max((b.price for b in market_depth.bids), default=0)
        best_ask = min((a.price for a in market_depth.asks), default=1)
        total_bid_liquidity = sum(b.size * b.price for b in market_depth.bids)
        total_ask_liquidity = sum(a.size * a.price for a in market_depth.asks)
        spread = best_ask - best_bid if best_ask and best_bid else 0
        
        # Format outcomes
        outcomes_str = "Unknown"
        if market_metadata.outcomes:
            outcomes_str = ", ".join(
                f"{outcome}: {price:.1%}" 
                for outcome, price in market_metadata.outcomes.items()
            )
        
        prompt = f"""You are an expert prediction market analyst. Analyze the following trade opportunity and provide a recommendation.

**IMPORTANT: The current date and time is {current_time}. Use this to calculate how long until the market resolves. Do NOT use your training data cutoff date.**

## Market Information
- **Title**: {market_metadata.title}
- **Question**: {market_metadata.question}
- **Category**: {market_metadata.category or 'Unknown'}
- **Status**: {market_metadata.status or 'Unknown'}
- **End Date**: {market_metadata.end_date or 'Unknown'}
- **Volume**: {f'${market_metadata.volume:,.2f}' if market_metadata.volume else 'Unknown'}
- **Current Outcomes**: {outcomes_str}

## Order Book Analysis
- **Best Bid**: ${best_bid:.2f}
- **Best Ask**: ${best_ask:.2f}
- **Spread**: ${spread:.4f} ({spread/best_ask*100:.2f}% of ask)
- **Bid Liquidity**: ${total_bid_liquidity:,.2f}
- **Ask Liquidity**: ${total_ask_liquidity:,.2f}

## Trade Context
- **Signal Source**: Whale trader "{context.get('whale_name', 'Unknown')}"
- **Whale Trade Size**: ${context.get('whale_trade_size', 0):,.2f}
- **Outcome Being Traded**: {context.get('outcome', 'Unknown')}
- **Trade Direction**: BUY (mirroring whale)

## Analysis Requirements
Please analyze this trade opportunity considering:

1. **Subjectivity Assessment**: How subjective vs objective is the outcome? (Sports scores are objective, political opinions are subjective)
2. **Resolution Timeline**: Calculate the time from NOW ({current_time}) until the end date. Is the timing favorable?
3. **Event Likelihood**: Based on current prices and any knowledge, how likely is the outcome being traded?
4. **Liquidity Risk**: Is there enough liquidity to enter and exit this position?
5. **Market Efficiency**: Does the current price seem efficient or is there potential mispricing?
6. **Whale Signal Strength**: Is following this whale likely to be profitable based on the trade size and market conditions?
7. **Risk Factors**: What could go wrong with this trade?
8. **Opportunity Factors**: What makes this trade attractive?

## Required Output Format
Respond with a JSON object in exactly this format (no markdown, no code blocks, just JSON):
{{
    "should_trade": true or false,
    "confidence": 0.0 to 1.0,
    "justification": "2-3 sentence summary of your recommendation",
    "risk_factors": ["risk 1", "risk 2", ...],
    "opportunity_factors": ["opportunity 1", "opportunity 2", ...],
    "estimated_resolution_time": "e.g., 2 hours, 2 days, 1 week, 3 months (calculate from current time {current_time})",
    "subjectivity_score": 0.0 (fully objective) to 1.0 (fully subjective)
}}

Provide your analysis:"""
        
        return prompt
    
    async def analyze_trade(
        self, 
        token_id: str, 
        market_metadata: MarketMetadata,
        market_depth: MarketDepth,
        context: dict
    ) -> TradeAnalysis:
        """
        Analyze whether a trade should be executed using Gemini AI.
        
        Returns TradeAnalysis with recommendation and justification.
        Includes retry logic with exponential backoff for rate limit errors.
        """
        if not self.api_key:
            # No API key - return default approval
            logger.warning("No Gemini API key - defaulting to approve trade")
            return TradeAnalysis(
                should_trade=True,
                confidence=0.5,
                justification="AI analysis unavailable (no API key configured)",
                risk_factors=["AI analysis not performed"],
                opportunity_factors=[]
            )
        
        prompt = self._build_analysis_prompt(token_id, market_metadata, market_depth, context)
        
        max_retries = 3
        base_delay = 1.0
        
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        f"{self.base_url}/models/{self.model}:generateContent",
                        params={"key": self.api_key},
                        json={
                            "contents": [{"parts": [{"text": prompt}]}],
                            "generationConfig": {
                                "temperature": 0.3,  # Lower temperature for more consistent analysis
                                "topP": 0.8,
                                "maxOutputTokens": 1024
                            }
                        }
                    )
                    
                    # Handle rate limiting with retry
                    if response.status_code == 429:
                        if attempt < max_retries - 1:
                            delay = base_delay * (2 ** attempt)
                            logger.warning(f"🚦 Rate limited by Gemini API, retrying in {delay}s (attempt {attempt + 1}/{max_retries})")
                            await asyncio.sleep(delay)
                            continue
                        else:
                            logger.error("Rate limit persists after max retries")
                            return self._fallback_analysis("Rate limit exceeded")
                    
                    if response.status_code != 200:
                        logger.error(f"Gemini API error: {response.status_code} - {response.text}")
                        return self._fallback_analysis("API error")
                    
                    data = response.json()
                    
                    # Extract text from response
                    candidates = data.get("candidates", [])
                    if not candidates:
                        logger.error("No candidates in Gemini response")
                        return self._fallback_analysis("Empty response")
                    
                    text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                    
                    # Parse JSON from response
                    return self._parse_response(text)
                    
            except httpx.TimeoutException:
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"Gemini API timeout, retrying in {delay}s (attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(delay)
                    continue
                logger.error("Gemini API timeout after max retries")
                return self._fallback_analysis("Request timeout")
            except Exception as e:
                logger.error(f"Gemini API error: {e}")
                return self._fallback_analysis(str(e))
    
    def _parse_response(self, text: str) -> TradeAnalysis:
        """Parse the Gemini response into a TradeAnalysis object."""
        try:
            # Try to extract JSON from the response
            # Handle potential markdown code blocks
            text = text.strip()
            if text.startswith("```"):
                # Remove code block markers
                lines = text.split("\n")
                text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
            
            data = json.loads(text)
            
            return TradeAnalysis(
                should_trade=bool(data.get("should_trade", True)),
                confidence=float(data.get("confidence", 0.5)),
                justification=str(data.get("justification", "Analysis completed")),
                risk_factors=list(data.get("risk_factors", [])),
                opportunity_factors=list(data.get("opportunity_factors", [])),
                estimated_resolution_time=data.get("estimated_resolution_time"),
                subjectivity_score=float(data.get("subjectivity_score")) if data.get("subjectivity_score") is not None else None
            )
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning(f"Failed to parse Gemini response: {e}")
            logger.debug(f"Raw response: {text[:500]}")
            return self._fallback_analysis("Failed to parse response")
    
    def _fallback_analysis(self, reason: str) -> TradeAnalysis:
        """Return a fallback analysis when AI fails - BLOCKS trades for safety."""
        return TradeAnalysis(
            should_trade=False,  # Block trades when AI is unavailable for safety
            confidence=0.0,
            justification=f"AI analysis unavailable ({reason}). Blocking trade for safety.",
            risk_factors=["AI analysis not performed", reason],
            opportunity_factors=[]
        )

    async def is_sports_market(
        self,
        market_metadata: MarketMetadata
    ) -> tuple[bool, str]:
        """
        Detect if a market is sports-related using Gemini AI.
        
        Args:
            market_metadata: Market details with title, question, category
            
        Returns:
            (is_sports, reason) - True if market should be blocked
        """
        if not self.api_key:
            logger.warning("No Gemini API key - cannot classify sports markets, blocking trade")
            return True, "No API key - blocking trade for safety"
        
        title = market_metadata.title or ""
        question = market_metadata.question or ""
        category = market_metadata.category or "Unknown"
        
        prompt = f"""Analyze this prediction market and determine if it is related to sports.

Market Title: {title}
Market Question: {question}
Category: {category}

A market is considered "sports-related" if it involves:
- Professional or amateur sports leagues (NFL, NBA, MLB, NHL, MLS, NCAA, etc.)
- Sporting events, games, matches, or competitions
- Athletes, teams, or sports organizations
- Sports betting outcomes (scores, winners, player performance)
- E-sports or competitive gaming tournaments
- College sports (NCAA basketball, football, etc.)

Respond with ONLY a JSON object in this format (no markdown, no code blocks):
{{"is_sports": true or false, "reason": "brief explanation"}}"""

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    f"{self.base_url}/models/{self.model}:generateContent",
                    params={"key": self.api_key},
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {
                            "temperature": 0.1,
                            "maxOutputTokens": 256
                        }
                    }
                )
                
                if response.status_code != 200:
                    logger.warning(f"Sports classification API error: {response.status_code}")
                    return True, f"AI classification failed (HTTP {response.status_code}) - blocking trade for safety"
                
                data = response.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    return False, "AI returned no classification"
                
                text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                text = text.strip()
                
                # Parse JSON response
                if text.startswith("```"):
                    lines = text.split("\n")
                    text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
                
                result = json.loads(text)
                is_sports = bool(result.get("is_sports", False))
                reason = result.get("reason", "AI classification")
                
                return is_sports, reason
                    
        except Exception as e:
            logger.warning(f"Sports AI classification error: {e}")
            return True, f"AI classification error - blocking trade for safety"

    async def is_crypto_price_market(
        self,
        market_metadata: MarketMetadata
    ) -> tuple[bool, str]:
        """
        Detect if a market is a crypto price prediction bet using Gemini AI.
        
        Examples: "Will BTC hit $100K?", "ETH above $4000 by March?"
        
        Args:
            market_metadata: Market details with title, question, category
            
        Returns:
            (is_crypto, reason) - True if market is crypto price prediction
        """
        if not self.api_key:
            logger.warning("No Gemini API key - cannot classify crypto markets")
            return False, "No API key - cannot classify"
        
        title = market_metadata.title or ""
        question = market_metadata.question or ""
        category = market_metadata.category or "Unknown"
        
        prompt = f"""Analyze this prediction market and determine if it is a CRYPTO PRICE PREDICTION bet.

Market Title: {title}
Market Question: {question}
Category: {category}

A market is a "crypto price prediction" if it involves:
- Predicting whether a cryptocurrency will go UP or DOWN
- Predicting whether a crypto will hit a specific price target
- Predicting crypto price movements within a specific timeframe
- Examples: "Will Bitcoin hit $100K?", "Will ETH be above $4000 by March?", "Will SOL go up in 24 hours?"

This does NOT include:
- General crypto news/events (e.g., "Will Coinbase launch X?")
- Crypto regulation questions
- NFT-related questions
- Questions about crypto companies

Respond with ONLY a JSON object in this format (no markdown, no code blocks):
{{"is_crypto_price": true or false, "reason": "brief explanation"}}"""

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    f"{self.base_url}/models/{self.model}:generateContent",
                    params={"key": self.api_key},
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {
                            "temperature": 0.1,
                            "maxOutputTokens": 256
                        }
                    }
                )
                
                if response.status_code != 200:
                    logger.warning(f"Crypto classification API error: {response.status_code}")
                    return False, f"AI classification failed (HTTP {response.status_code})"
                
                data = response.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    return False, "AI returned no classification"
                
                text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                text = text.strip()
                
                # Parse JSON response
                if text.startswith("```"):
                    lines = text.split("\n")
                    text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
                
                result = json.loads(text)
                is_crypto = bool(result.get("is_crypto_price", False))
                reason = result.get("reason", "AI classification")
                
                return is_crypto, reason
                    
        except Exception as e:
            logger.warning(f"Crypto AI classification error: {e}")
            return False, f"AI classification error"

    async def evaluate_sports_selectivity(
        self,
        market_metadata: MarketMetadata,
        max_days_to_resolution: float,
        min_favorite_odds: float
    ) -> SportsSelectivityResult:
        """
        Evaluate if a sports market qualifies for selective trading.
        
        Args:
            market_metadata: Market details with title, question, outcomes, end_date
            max_days_to_resolution: Maximum allowed days until resolution
            min_favorite_odds: Minimum odds required for the favorite (0.0 to 1.0)
            
        Returns:
            SportsSelectivityResult with qualification decision and details
        """
        if not self.api_key:
            logger.warning("No Gemini API key - cannot evaluate sports selectivity")
            return SportsSelectivityResult(
                qualifies=False,
                confidence=0.0,
                favorite_odds=0.0,
                justification="No API key - cannot evaluate"
            )
        
        title = market_metadata.title or ""
        question = market_metadata.question or ""
        category = market_metadata.category or "Unknown"
        end_date = market_metadata.end_date or "Unknown"
        outcomes_str = "Unknown"
        if market_metadata.outcomes:
            outcomes_str = ", ".join(
                f"{outcome}: {price:.1%}" 
                for outcome, price in market_metadata.outcomes.items()
            )
        
        from datetime import datetime
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M UTC")
        
        prompt = f"""Evaluate this sports prediction market for selective trading qualification.

**Current Time**: {current_time}
**Market Title**: {title}
**Market Question**: {question}
**Category**: {category}
**End Date**: {end_date}
**Current Outcome Prices**: {outcomes_str}

**Qualification Criteria**:
1. Time to Resolution: Market must resolve within {max_days_to_resolution} days from now
2. Favorite Odds: The leading outcome must have at least {min_favorite_odds:.0%} odds
3. Clear Favorite: There must be a clear favorite (team, club, or player)

**Analysis Required**:
1. Calculate hours until resolution from current time ({current_time}) to end date
2. Identify the favorite entity (team, club, or player name)
3. Determine if this is a high-confidence sports trade worth making

Respond with ONLY a JSON object in this format (no markdown, no code blocks):
{{
    "qualifies": true or false,
    "confidence": 0.0 to 1.0,
    "favorite_odds": 0.0 to 1.0 (the actual odds of the favorite),
    "hours_to_resolution": number or null,
    "favorite_entity": "Team/Player name" or null,
    "justification": "brief explanation of qualification decision"
}}"""

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    f"{self.base_url}/models/{self.model}:generateContent",
                    params={"key": self.api_key},
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {
                            "temperature": 0.2,
                            "maxOutputTokens": 512
                        }
                    }
                )
                
                if response.status_code != 200:
                    logger.warning(f"Sports selectivity API error: {response.status_code}")
                    return SportsSelectivityResult(
                        qualifies=False,
                        confidence=0.0,
                        favorite_odds=0.0,
                        justification=f"AI evaluation failed (HTTP {response.status_code})"
                    )
                
                data = response.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    return SportsSelectivityResult(
                        qualifies=False,
                        confidence=0.0,
                        favorite_odds=0.0,
                        justification="AI returned no evaluation"
                    )
                
                text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                text = text.strip()
                
                # Parse JSON response
                if text.startswith("```"):
                    lines = text.split("\n")
                    text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
                
                result = json.loads(text)
                
                return SportsSelectivityResult(
                    qualifies=bool(result.get("qualifies", False)),
                    confidence=float(result.get("confidence", 0.0)),
                    favorite_odds=float(result.get("favorite_odds", 0.0)),
                    hours_to_resolution=result.get("hours_to_resolution"),
                    favorite_entity=result.get("favorite_entity"),
                    justification=result.get("justification", "AI evaluation")
                )
                    
        except Exception as e:
            logger.warning(f"Sports selectivity AI evaluation error: {e}")
            return SportsSelectivityResult(
                qualifies=False,
                confidence=0.0,
                favorite_odds=0.0,
                justification=f"AI evaluation error: {e}"
            )

