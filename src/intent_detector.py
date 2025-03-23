import time
import asyncio
import logging
import re
from typing import List, Tuple, Dict, Optional, Any

logger = logging.getLogger(__name__)

class IntentDetector:
    """Detects user intent in messages using only pattern matching"""
    
    def __init__(self, model_name=None, api_key=None, api_throttler=None):
        """
        Initialize intent detector with API disabled
        
        Args:
            model_name: Ignored, kept for backwards compatibility
            api_key: Ignored, kept for backwards compatibility
            api_throttler: Ignored, kept for backwards compatibility
        """
        # Completely disable API calls
        self.api_disabled = True
        self.failed_calls_count = 0
        self.last_success_time = time.time()
        logger.info("Intent detector initialized with API_DISABLED=True")
    
    async def detect_intent(self, text: str) -> dict:
        """Use pattern matching only, no API calls"""
        return self._pattern_based_intent_detection(text)
    
    async def detect_argumentative_intent(self, text: str) -> tuple:
        """Use pattern matching only for argumentative intent"""
        # Skip API completely
        text_lower = text.lower()
        
        # Check for first-person pronouns
        is_self_referential = any(pronoun in text_lower.split()[:3] for pronoun in ["i", "me", "my", "i'm", "im"])
        
        # Check for insult words
        insult_words = ["suck", "trash", "garbage", "dumb", "stupid", "idiot", "shit", "fuck", "ass", "bad"]
        has_insult = any(word in text_lower.split() for word in insult_words)
        
        # If self-referential, it's likely not an insult toward the bot
        if is_self_referential and has_insult:
            return (False, "self_deprecation")
        elif has_insult:
            return (True, "insult")
        
        # Check for argumentative patterns
        arg_patterns = ["no ", "nope", "wrong", "incorrect", "not true", "you're not", "ur not"]
        if any(pattern in text_lower for pattern in arg_patterns):
            return (True, "disagreement")
            
        return (False, "neutral")
    
    async def detect_forget_intent(self, text: str) -> tuple:
        """Pattern-based detection for forget intent - returns tuple of (bool, str)"""
        # Skip API completely
        text_lower = text.lower()
        forget_patterns = ["/forget", "forget ", "don't remember", "forget about", "stop talking about"]
        
        if any(pattern in text_lower for pattern in forget_patterns):
            # Try to extract what to forget
            target = None
            if "forget about " in text_lower:
                target = text_lower.split("forget about ")[1].strip()
            elif "forget " in text_lower:
                target = text_lower.split("forget ")[1].strip()
            
            return (True, target or "all")  # Return tuple with is_forget and target
        
        return (False, None)  # Return tuple with is_forget and target
    
    async def detect_game_intent(self, text: str) -> tuple:
        """Pattern-based detection for game intent - returns tuple"""
        # Skip API completely
        text_lower = text.lower()
        game_patterns = ["/game", "play a game", "let's play", "play with me", "wanna play"]
        
        if any(pattern in text_lower for pattern in game_patterns):
            # Try to extract game type
            game_type = "default"
            if "guess" in text_lower:
                game_type = "guess"
            elif "20 questions" in text_lower:
                game_type = "20q"
                
            return (True, game_type)
        
        return (False, None)
    
    async def detect_end_game_intent(self, text: str) -> tuple:
        """Pattern-based detection for end game intent - returns tuple"""
        # Skip API completely
        text_lower = text.lower()
        end_patterns = ["end game", "stop game", "quit game", "exit game", "no more game"]
        
        if any(pattern in text_lower for pattern in end_patterns):
            return (True, "end")
        
        return (False, None)
    
    async def extract_guess(self, text: str) -> Optional[str]:
        """Extract guess using regex, no API"""
        # Skip API completely
        text_lower = text.lower()
        
        # Common guess patterns
        if "is it " in text_lower:
            return text_lower.split("is it ")[1].strip("?!. ")
        
        if "guess " in text_lower:
            return text_lower.split("guess ")[1].strip("?!. ")
            
        # Try to extract the most likely noun phrase
        if len(text_lower.split()) <= 3:
            # If text is short, just return it all
            return text_lower.strip("?!. ")
            
        return text_lower.strip("?!. ")
    
    async def detect_user_info_intent(self, text: str) -> tuple:
        """Pattern-based detection for user info requests - returns tuple"""
        # Skip API completely
        text_lower = text.lower()
        patterns = [
            "my age", "how old am i", "my name", "what do you know about me",
            "what do you know", "who am i", "where am i", "what am i", 
            "tell me about myself", "my info", "my information"
        ]
        
        if any(pattern in text_lower for pattern in patterns):
            # Try to detect what info they want
            info_type = "general"
            if "age" in text_lower or "old" in text_lower:
                info_type = "age"
            elif "name" in text_lower:
                info_type = "name"
            elif "where" in text_lower or "location" in text_lower:
                info_type = "location"
                
            return (True, info_type)
        
        return (False, None)
    
    async def detect_correction_intent(self, message: str) -> tuple:
        """Pattern-based detection for corrections - returns tuple of (bool, str)"""
        text_lower = message.lower()
        
        # Common correction patterns
        correction_patterns = [
            "no", "nope", "that's not", "that is not", "incorrect", "wrong", 
            "i didn't", "i did not", "i never said", "actually", "not true",
            "you're mistaken", "you are mistaken", "you misunderstood",
            "that's wrong", "that is wrong", "i meant", "you got it wrong",
            "you're confusing", "you are confusing", "i was referring to",
            "not what i said", "not what i meant", "you misinterpreted"
        ]
        
        # Check if message starts with or contains correction patterns
        for pattern in correction_patterns:
            # Check if pattern is at the beginning or preceded by a space
            if text_lower.startswith(pattern + " ") or f" {pattern} " in f" {text_lower} ":
                return (True, "correction")  # Return tuple instead of just boolean
        
        return (False, "no_correction")  # Return tuple instead of just boolean

    def _pattern_based_intent_detection(self, text: str) -> dict:
        """General intent detection using patterns only"""
        text_lower = text.lower()
        
        # Question detection
        if any(q in text_lower for q in ["?", "what", "who", "when", "where", "why", "how"]):
            return {"intent": "question", "confidence": 0.8}
            
        # Command detection
        if any(cmd in text_lower for cmd in ["/", "show me", "tell me", "give me", "find", "search"]):
            return {"intent": "command", "confidence": 0.9}
            
        # Emotional content
        if any(emotion in text_lower for emotion in ["happy", "sad", "angry", "love", "hate", "glad", "mad"]):
            return {"intent": "emotional", "confidence": 0.7}
            
        # Insult detection
        if any(insult in text_lower for insult in ["trash", "garbage", "suck", "bad", "terrible", "stupid", "dumb"]):
            return {"intent": "insult", "confidence": 0.9}
            
        # Default
        return {"intent": "statement", "confidence": 0.6}