import google.generativeai as genai
import json
import logging
import os
import sys
from typing import Dict, Any, List, Optional, Tuple
import re

# Try different import approaches to handle both direct and relative imports
try:
    # When run directly
    from config.config import Config
except ImportError:
    try:
        # When imported from elsewhere
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
        from config.config import Config
    except ImportError:
        # Fallback
        Config = None

class IntentDetector:
    """detects user intentions from messages using AI instead of fixed word lists"""
    
    def __init__(self, model_name="gemini-1.5-flash-latest"):
        """set up the intent detector with a lightweight model"""
        self.model = genai.GenerativeModel(model_name)
        self.logger = logging.getLogger("intent_detector")
        
        # cache recent detections to avoid repeated calls for similar messages
        self.cache = {}
        self.cache_size = 100
        
        # Store common patterns for fallback
        self.common_patterns = {
            "correction": [
                r"(?i)(that'?s|thats|is) (not|wrong|incorrect)",
                r"(?i)i (meant|mean|didn'?t mean)",
                r"(?i)actually[,]?",
                r"(?i)correction",
                r"(?i)fix (that|this)",
                r"(?i)that (should|needs to) be"
            ],
            "forget": [
                r"(?i)(forget|delete|remove|erase) (about|that|this|my|the)",
                r"(?i)don'?t (remember|keep) (that|this|my)",
                r"(?i)clear my",
                r"(?i)wipe (my|the) data"
            ],
            "game": [
                r"(?i)(start|play|begin) (a |the )?game",
                r"(?i)let'?s play",
                r"(?i)wanna play",
                r"(?i)^play$",
                r"(?i)new game"
            ],
            "end_game": [
                r"(?i)(end|stop|quit|exit|finish) (the |this )?game",
                r"(?i)i('?m| am) done",
                r"(?i)stop playing",
                r"(?i)^end$",
                r"(?i)give up"
            ],
            "user_info": [
                r"(?i)what (do you|you) know about me",
                r"(?i)(show|tell|give) me my (info|data|facts)",
                r"(?i)my (info|data|profile)",
                r"(?i)what('?s| is) stored",
                r"(?i)what have (you|we) talked about"
            ],
            "argumentative": [
                r"(?i)(fuck|shit|damn|bitch|stfu|shut up|bullshit)",
                r"(?i)(you'?re|your|ur|you) (wrong|stupid|dumb|idiot|retarded)",
                r"(?i)(you'?re|your|ur|you) (bad|terrible|awful|useless)",
                r"(?i)i hate (you|this|that|the bot)",
                r"(?i)(no way|nah|not true|cap)",
                r"(?i)(you|u) (don'?t|dont) know (what|anything)",
                r"(?i)that'?s (stupid|dumb|idiotic|moronic)"
            ]
        }
        
        # Precompile regex patterns for efficiency
        self.compiled_patterns = {
            intent: [re.compile(pattern) for pattern in patterns]
            for intent, patterns in self.common_patterns.items()
        }
        
    def _check_patterns(self, message: str, intent_type: str) -> bool:
        """Check message against precompiled regex patterns for an intent type"""
        if intent_type not in self.compiled_patterns:
            return False
            
        return any(
            pattern.search(message) is not None
            for pattern in self.compiled_patterns[intent_type]
        )
        
    async def detect_correction_intent(self, message: str) -> bool:
        """detect if user is trying to correct previously stored info"""
        if message in self.cache and "correction" in self.cache[message]:
            return self.cache[message]["correction"]
            
        # First try pattern matching for speed
        if self._check_patterns(message, "correction"):
            result = True
        else:
            # Fall back to AI for more nuanced cases
            prompt = f"""
            Determine if this message is trying to CORRECT previously stored information.
            Return true if the user is:
            - Correcting something the bot got wrong
            - Stating that something is incorrect 
            - Trying to fix misunderstood information
            - Using phrases like "actually", "that's wrong", "I meant", "that's not right"
            - Implying something needs to be corrected
            - Providing clarification that contradicts previous information
            
            Return just true or false (lowercase).
            
            Message: {message}
            """
            
            try:
                response = await self.model.generate_content_async(prompt)
                result = response.text.strip().lower() == "true"
            except Exception as e:
                self.logger.error(f"Error detecting correction intent: {e}")
                # Fall back to pattern matching result
                result = False
            
        # Cache the result
        if len(self.cache) >= self.cache_size:
            self.cache.pop(next(iter(self.cache)))
        self.cache[message] = {"correction": result}
            
        return result
    
    async def detect_forget_intent(self, message: str) -> Tuple[bool, Optional[str]]:
        """detect if user wants to forget specific data and extract what to forget"""
        if message in self.cache and "forget" in self.cache[message]:
            return self.cache[message]["forget"]
            
        # First try pattern matching
        if self._check_patterns(message, "forget"):
            # Try to extract target from message
            words = message.lower().split()
            for i, word in enumerate(words):
                if word in ["forget", "delete", "remove", "erase", "clear"]:
                    target = " ".join(words[i+1:]).strip()
                    if target:
                        result = (True, target)
                        break
            else:
                result = (True, None)
        else:
            # Fall back to AI for complex cases
            prompt = f"""
            Determine if this message is asking to DELETE previously stored information.
            Consider both direct requests and implied intentions to remove data.
            
            If it is, respond with JSON: {{"intent": true, "target": "what should be forgotten"}}
            If not, respond with JSON: {{"intent": false, "target": null}}
            
            Examples:
            - "forget what I said about cats" -> {{"intent": false, "target": "null"}}
            - "delete my info about school" -> {{"intent": true, "target": "school"}}
            - "delete my birthday info" -> {{"intent": true, "target": "birthday"}}
            - "remove everything" -> {{"intent": true, "target": null}}
            - "what's up" -> {{"intent": false, "target": null}}
            - "can you not remember that" -> {{"intent": true, "target": "that(depends on the context)"}}
            
            Message: {message}
            """
            
            try:
                response = await self.model.generate_content_async(prompt)
                result_text = response.text.strip()
                
                # Handle markdown formatting
                if "```json" in result_text:
                    result_text = result_text.split("```json")[1].split("```")[0].strip()
                elif "```" in result_text:
                    result_text = result_text.split("```")[1].strip()
                    
                result_data = json.loads(result_text)
                result = (result_data["intent"], result_data["target"])
            except Exception as e:
                self.logger.error(f"Error detecting forget intent: {e}")
                result = (False, None)
                
        # Cache the result
        if len(self.cache) >= self.cache_size:
            self.cache.pop(next(iter(self.cache)))
        self.cache[message] = {"forget": result}
            
        return result
    
    async def detect_game_intent(self, message: str) -> bool:
        """detect if user wants to play a game"""
        if message in self.cache and "game" in self.cache[message]:
            return self.cache[message]["game"]
            
        # First try pattern matching
        if self._check_patterns(message, "game"):
            result = True
        else:
            # Fall back to AI for complex cases
            prompt = f"""
            Determine if this message is trying to START or PLAY a GAME.
            Consider both direct requests and implied intentions to play.
            
            Return true if the user is:
            - Asking to play a game
            - Wanting to start a game
            - Requesting to play
            - Using phrases related to starting games
            - Showing interest in playing
            - Making indirect requests to play
            
            Return just true or false (lowercase).
            
            Message: {message}
            """
            
            try:
                response = await self.model.generate_content_async(prompt)
                result = response.text.strip().lower() == "true"
            except Exception as e:
                self.logger.error(f"Error detecting game intent: {e}")
                result = False
                
        # Cache the result
        if len(self.cache) >= self.cache_size:
            self.cache.pop(next(iter(self.cache)))
        self.cache[message] = {"game": result}
            
        return result
    
    async def detect_end_game_intent(self, message: str) -> bool:
        """detect if user wants to end a game"""
        if message in self.cache and "end_game" in self.cache[message]:
            return self.cache[message]["end_game"]
            
        # First try pattern matching
        if self._check_patterns(message, "end_game"):
            result = True
        else:
            # Fall back to AI for complex cases
            prompt = f"""
            Determine if this message is trying to END or STOP a GAME.
            Consider both direct requests and implied intentions to stop playing.
            
            Return true if the user is:
            - Asking to end a game
            - Wanting to stop playing
            - Requesting to quit a game
            - Using phrases related to ending games
            - Showing frustration or desire to stop
            - Making indirect requests to end the game
            
            Return just true or false (lowercase).
            
            Message: {message}
            """
            
            try:
                response = await self.model.generate_content_async(prompt)
                result = response.text.strip().lower() == "true"
            except Exception as e:
                self.logger.error(f"Error detecting end game intent: {e}")
                result = False
                
        # Cache the result
        if len(self.cache) >= self.cache_size:
            self.cache.pop(next(iter(self.cache)))
        self.cache[message] = {"end_game": result}
            
        return result
    
    async def extract_guess_value(self, message: str) -> Optional[int]:
        """extract a numeric guess from a message"""
        if message in self.cache and "guess" in self.cache[message]:
            return self.cache[message]["guess"]
            
        # First try direct number extraction
        numbers = re.findall(r'\b\d+\b', message)
        if numbers:
            try:
                guess = int(numbers[0])
                # Cache and return if found
                if len(self.cache) >= self.cache_size:
                    self.cache.pop(next(iter(self.cache)))
                self.cache[message] = {"guess": guess}
                return guess
            except ValueError:
                pass
                
        # Fall back to AI for complex cases
        prompt = f"""
        Extract a NUMERIC GUESS from this message if present.
        Consider both direct numbers and written numbers.
        Convert written numbers to digits.
        
        Return just the number or null if no clear guess is found.
        
        Examples:
        - "I guess 42" -> 42
        - "maybe it's seventeen?" -> 17
        - "let me try ninety-nine" -> 99
        - "is it forty two" -> 42
        - "what's up" -> null
        
        Message: {message}
        """
        
        try:
            response = await self.model.generate_content_async(prompt)
            result_text = response.text.strip().lower()
            
            # Try to convert to number
            if result_text == "null" or not result_text:
                guess = None
            else:
                try:
                    guess = int(result_text)
                except ValueError:
                    guess = None
                    
            # Cache the result
            if len(self.cache) >= self.cache_size:
                self.cache.pop(next(iter(self.cache)))
            self.cache[message] = {"guess": guess}
            
            return guess
        except Exception as e:
            self.logger.error(f"Error extracting guess: {e}")
            return None
    
    async def detect_user_info_intent(self, message: str) -> bool:
        """detect if user is asking about their stored info"""
        if message in self.cache and "user_info" in self.cache[message]:
            return self.cache[message]["user_info"]
            
        # First try pattern matching
        if self._check_patterns(message, "user_info"):
            result = True
        else:
            # Fall back to AI for complex cases
            prompt = f"""
            Determine if this message is asking to SEE or GET the user's own stored information.
            Consider both direct requests and implied intentions to view data.
            
            Return true if the user is:
            - Asking what info the bot has about them
            - Requesting to see their stored data
            - Asking what the bot knows about them
            - Using phrases about retrieving their information
            - Making indirect requests about their data
            - Showing interest in what's been remembered
            
            Return just true or false (lowercase).
            
            Message: {message}
            """
            
            try:
                response = await self.model.generate_content_async(prompt)
                result = response.text.strip().lower() == "true"
            except Exception as e:
                self.logger.error(f"Error detecting user info intent: {e}")
                result = False
                
        # Cache the result
        if len(self.cache) >= self.cache_size:
            self.cache.pop(next(iter(self.cache)))
        self.cache[message] = {"user_info": result}
            
        return result
    
    async def detect_argumentative_intent(self, message: str) -> Tuple[bool, str]:
        """detect if user is arguing with or insulting the bot"""
        if message in self.cache and "argumentative" in self.cache[message]:
            return self.cache[message]["argumentative"]
            
        # First try pattern matching for speed
        if self._check_patterns(message, "argumentative"):
            # Try to classify the type of argument
            if re.search(r"(?i)(fuck|shit|damn|bitch|stfu|shut up)", message):
                arg_type = "insult"
            elif re.search(r"(?i)(wrong|incorrect|not true|cap)", message):
                arg_type = "disagreement"
            elif re.search(r"(?i)(stupid|dumb|idiot|retarded)", message):
                arg_type = "criticism"
            else:
                arg_type = "general"
                
            result = (True, arg_type)
        else:
            # Fall back to AI for complex cases
            prompt = f"""
            Determine if this message is ARGUMENTATIVE, INSULTING, or CHALLENGING the bot.
            Consider direct insults, arguments, disagreements, and challenging statements.
            
            If it is, respond with JSON: {{"intent": true, "type": "<type>"}}
            If not, respond with JSON: {{"intent": false, "type": null}}
            
            Type should be one of:
            - "insult" (direct insults or profanity directed at bot)
            - "disagreement" (user saying bot is wrong)
            - "criticism" (user criticizing bot's abilities)
            - "challenge" (user challenging bot's knowledge/skills)
            - "general" (other forms of argument)
            
            Examples:
            - "you're so fucking stupid" -> {{"intent": true, "type": "insult"}}
            - "that's wrong, actually it's 42" -> {{"intent": true, "type": "disagreement"}}
            - "you don't know what you're talking about" -> {{"intent": true, "type": "criticism"}}
            - "bet you can't solve this" -> {{"intent": true, "type": "challenge"}}
            - "whatever, this bot sucks" -> {{"intent": true, "type": "general"}}
            - "hi how are you today" -> {{"intent": false, "type": null}}
            
            Message: {message}
            """
            
            try:
                response = await self.model.generate_content_async(prompt)
                result_text = response.text.strip()
                
                # Handle markdown formatting
                if "```json" in result_text:
                    result_text = result_text.split("```json")[1].split("```")[0].strip()
                elif "```" in result_text:
                    result_text = result_text.split("```")[1].strip()
                    
                result_data = json.loads(result_text)
                result = (result_data["intent"], result_data.get("type", "general") if result_data["intent"] else None)
            except Exception as e:
                self.logger.error(f"Error detecting argumentative intent: {e}")
                result = (False, None)
                
        # Cache the result
        if len(self.cache) >= self.cache_size:
            self.cache.pop(next(iter(self.cache)))
        self.cache[message] = {"argumentative": result}
            
        return result 