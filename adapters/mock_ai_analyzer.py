"""
Mock AI Analyzer Adapter

A mock implementation of AIAnalysisProvider for testing and dry-run mode.
Always approves trades with a logged analysis.
"""

import logging
from polybot.core.interfaces import AIAnalysisProvider
from polybot.core.models import TradeAnalysis, MarketMetadata, MarketDepth

logger = logging.getLogger(__name__)


class MockAIAnalyzerAdapter(AIAnalysisProvider):
    """Mock AI analyzer that always approves trades for testing."""
    
    def __init__(self, default_approval: bool = True):
        """
        Initialize mock analyzer.
        
        Args:
            default_approval: If True, always approves trades. If False, always rejects.
        """
        self.default_approval = default_approval
        logger.info(f"🤖 MockAIAnalyzer initialized (default_approval={default_approval})")
    
    async def analyze_trade(
        self, 
        token_id: str, 
        market_metadata: MarketMetadata,
        market_depth: MarketDepth,
        context: dict
    ) -> TradeAnalysis:
        """
        Mock trade analysis - returns predetermined result.
        
        Useful for testing the integration without making API calls.
        """
        logger.info(f"🤖 [MOCK] Analyzing trade for: {market_metadata.title}")
        
        if self.default_approval:
            return TradeAnalysis(
                should_trade=True,
                confidence=1.0,
                justification="Mock analyzer - automatically approved for testing",
                risk_factors=[],
                opportunity_factors=["Mock mode enabled", "Testing configuration"],
                estimated_resolution_time=None,
                subjectivity_score=None
            )
        else:
            return TradeAnalysis(
                should_trade=False,
                confidence=1.0,
                justification="Mock analyzer - configured to reject all trades",
                risk_factors=["Mock rejection mode enabled"],
                opportunity_factors=[],
                estimated_resolution_time=None,
                subjectivity_score=None
            )
