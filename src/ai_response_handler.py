import asyncio
import random
import re
import logging
import time  # Add this import
import google.generativeai as genai
from typing import List, Tuple, Dict, Any, Optional
from config.ai_config import PERSONALITY_PROMPT
import json
from collections import deque

# Setup logging
logger = logging.getLogger(__name__)

class AIResponseHandler:
    """Handles all AI response generation logic"""
    
    def __init__(self, api_key: str, important_topics: List[str], api_throttler=None, user_data_manager=None):
        """Initialize AI response handler without intent detection"""
        self.important_topics = important_topics
        self.api_throttler = api_throttler
        self.user_data_manager = user_data_manager
        # No intent_detector attribute
        
        # Response cache
        self.response_cache = {}
        self.cache_size = 50
        
        # Set up AI model
        genai.configure(api_key=api_key)
        self.ai_model = genai.GenerativeModel("gemini-1.5-flash-latest")
        
        # Fallback responses
        self.fallback_responses = deque([
            "yo my neural nets are fried rn, gimme a sec",
            "bruh i think im gettin rate limited, one sec",
            "damn, quota issues again? my dev needs to pay up fr",
            "ngl my brain just glitched for a sec, try again?",
            "lmao my processor just overheated, brb",
            "yo whoever coded me forgot to pay the brain bill 😭",
            "shit i think my dev's api quota just got clapped lol",
            "damn, can't think straight rn, try again in a bit?",
            "bruh im lagging so hard rn, gimme a min",
            "my brain cells just went on strike fr fr"
        ], maxlen=10)
        
        # Add reference to user data manager
        self.user_data_manager = user_data_manager
    
    def extract_important_topics(self, message_content: str) -> List[str]:
        """Find sensitive topics in messages more efficiently"""
        # Early return for empty messages
        if not message_content:
            return []
            
        # Convert to lowercase once for efficiency
        message_lower = message_content.lower()
        
        # Use set comprehension for faster lookup
        return [topic for topic in self.important_topics 
                if topic in message_lower]
    
    async def _build_ai_prompt(self, query: str, conversation_history: str, username: str, user_id: str = None) -> str:
        """Build the prompt with better conversation context"""
        prompt_parts = []
        
        # Add personality prompt first
        prompt_parts.append(PERSONALITY_PROMPT)
        
        # Add critical instructions for conversation awareness
        prompt_parts.append("""
        CRITICAL CONVERSATION INSTRUCTIONS:
        1. ALWAYS stay on the SAME TOPIC when responding to follow-up questions
        2. If asked "why", "why tho", or to give "more reason", explain YOUR previous statements
        3. When a user asks about "which one", ALWAYS refer to options YOU mentioned previously
        4. READ and UNDERSTAND the conversation history before responding
        5. NEVER change the subject randomly - maintain conversation flow
        6. REMEMBER what YOU said in your previous messages
        7. Short user questions (1-5 words) are ALWAYS about what YOU just mentioned
        8. DO NOT introduce unrelated topics (like politics or conspiracy theories) unless relevant
        """)
        
        # Add conversation history with emphasis on tracking topics
        if conversation_history:
            # Add the conversation history with emphasis on continuity
            prompt_parts.append(f"""
            RECENT CONVERSATION CONTEXT:
            {conversation_history}
            
            IMPORTANT: The conversation is ongoing. The user's current message should be understood
            in the context of what was just discussed. Maintain topic continuity.
            """)
        
        # Add user query
        prompt_parts.append(f"User ({username}) just said: \"{query}\"")
        
        # Add response format guidance
        prompt_parts.append("""
        Your response as ChronoChunk:
        - Keep your Gen Z style
        - STAY ON THE CURRENT TOPIC
        - DIRECTLY address what the user just asked
        - If they asked "why" or "why tho", explain YOUR previous statement
        - If they asked about "which one", tell them your favorite of whatever YOU just mentioned
        """)
        
        return "\n\n".join(prompt_parts)
        
    def _format_ai_response(self, raw_response: str) -> str:
        """Format the AI response for more natural style"""
        if not raw_response:
            return "brain fried, hit me up again"
            
        # Extract the actual response text
        ai_response = raw_response.strip()
        
        # Remove any leading indicators
        ai_response = re.sub(r'^(You:|Your response:|ChronoChunk:|Response:|Bot:)\s*', '', ai_response, flags=re.IGNORECASE)
        
        # Force lowercase with 95% probability (allows rare capitals for emphasis)
        if random.random() < 0.95:
            ai_response = ai_response.lower()
        
        # Fix multiple question marks - never more than two
        ai_response = re.sub(r'\?{3,}', '??', ai_response)
        
        # Fix multiple exclamation marks - never more than two
        ai_response = re.sub(r'!{3,}', '!!', ai_response)
        
        # Fix spacing around punctuation
        ai_response = re.sub(r'\s+([.,?!])', r'\1', ai_response)
        
        # Detect if this is a list or explanation (needs longer format)
        is_list = any(marker in ai_response.lower() for marker in [": ", "- ", "1. ", "first", "second", "third", "lastly", "finally"]) 
        is_explanation = any(marker in ai_response.lower() for marker in ["because", "reason", "explain", "works by", "basically", "fundamentally"])
        needs_long_format = is_list or is_explanation or len(ai_response) > 200
        
        # Handle repetitive phrases (more comprehensive list)
        repetitive_phrases = [
            "damn bro",
            "u fr think",
            "u really think",
            "my g",
            "fr fr",
            "no cap no cap",
            "u sayin",
            "like fr",
            "for real",
            "u know what im sayin",
            "deadass"
        ]
        
        # Use a more sophisticated approach to reduce repetition
        for phrase in repetitive_phrases:
            if ai_response.count(phrase) > 1:
                # Keep first occurrence, replace others with alternatives
                parts = ai_response.split(phrase, 1)
                alternatives = {
                    "damn bro": ["bruh", "yo", "listen", "lmao", "fr tho", "ngl", "honestly"],
                    "u fr think": ["u actually think", "u believe", "u really out here thinkin", "u convinced"],
                    "u really think": ["u seriously think", "u out here thinkin", "u got the idea that"],
                    "my g": ["bro", "dude", "fam", "homie", "dawg"],
                    "fr fr": ["no cap", "deadass", "on god", "straight up"],
                    "no cap no cap": ["fr fr", "deadass", "on god", "no lie"],
                    "u sayin": ["u mean", "u tellin me", "ur point is"],
                    "like fr": ["deadass", "no joke", "seriously", "for real"],
                    "for real": ["fr", "no cap", "deadass", "on god"],
                    "u know what im sayin": ["ya feel me", "get me", "u follow"],
                    "deadass": ["fr", "no cap", "on god", "straight up"]
                }
                
                replacement = random.choice(alternatives.get(phrase, [""])) if phrase in alternatives else ""
                if replacement:
                    replaced_text = parts[0] + phrase
                    for part in parts[1:]:
                        replaced_text += part.replace(phrase, replacement, 1) 
                    ai_response = replaced_text
        
        # Sentence length handling
        sentences = re.split(r'(?<=[.!?])\s+', ai_response)
        
        # Allow longer responses for lists and explanations
        max_sentences = 6 if needs_long_format else 3
        
        # Don't truncate if we need the longer format
        if len(sentences) > max_sentences and not needs_long_format:
            ai_response = ' '.join(sentences[:max_sentences])
        
        # Emoji handling with more variety
        emoji_pattern = re.compile(r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F700-\U0001F77F\U0001F780-\U0001F7FF\U0001F800-\U0001F8FF\U0001F900-\U0001F9FF\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF\U00002702-\U000027B0\U000024C2-\U0001F251]+')
        emojis = emoji_pattern.findall(ai_response)
        
        # Replace with varied emoji based on message tone
        response_tone = "neutral"  # default
        
        # Detect message tone
        if any(word in ai_response.lower() for word in ["fuck", "shit", "ass", "stfu", "damn", "trash", "garbage", "suck", "loser"]):
            response_tone = "aggressive"
        elif any(word in ai_response.lower() for word in ["lol", "lmao", "haha", "joke", "funny", "laughing"]):
            response_tone = "humorous"
        elif any(word in ai_response.lower() for word in ["sorry", "sad", "hurt", "pain", "sick", "ill", "cancer", "died"]):
            response_tone = "sympathetic"
        
        # Emoji mappings by tone
        emoji_options = {
            "aggressive": ["💀", "😤", "😈", "🔥", "👊", "🖕", "😒", "🤡", "😑"],
            "humorous": ["💀", "😭", "😂", "🤣", "💅", "👀", "😩", "🥴", "🙃"],
            "sympathetic": [ "😔", "🙏", "❤️", "🫂", "💯", "🥺", "✨"],
            "neutral": ["💀", "😭", "🔥", "🙏", "😤", "💯", "🤷", "👀"]
        }
        
        # If we have too many emojis, keep just one that matches the tone
        if len(emojis) > 1:
            # Remove all emojis
            for emoji in emojis:
                ai_response = ai_response.replace(emoji, '')
            
            # Add one emoji that matches the tone at a natural location (preferably end of sentence)
            chosen_emoji = random.choice(emoji_options[response_tone])
            
            # Find a good spot for the emoji (end of a sentence)
            sentence_ends = [m.end() for m in re.finditer(r'[.!?]\s', ai_response)]
            if sentence_ends:
                insert_pos = random.choice(sentence_ends)
                ai_response = ai_response[:insert_pos] + f" {chosen_emoji}" + ai_response[insert_pos:]
            else:
                # If no good spot, just append to the end
                ai_response += f" {chosen_emoji}"
        # If no emojis, randomly add one 80% of the time
        elif not emojis and random.random() < 0.8:
            chosen_emoji = random.choice(emoji_options[response_tone])
            # Try to add it at the end of a sentence
            sentence_ends = [m.end() for m in re.finditer(r'[.!?]\s', ai_response)]
            if sentence_ends:
                insert_pos = random.choice(sentence_ends)
                ai_response = ai_response[:insert_pos] + f" {chosen_emoji}" + ai_response[insert_pos:]
            else:
                # If no good spot, just append to the end
                ai_response += f" {chosen_emoji}"
        
        # Fix specific phrases that don't match desired style
        style_fixes = {
            "u fr think dat": "u think that",
            "u fr think teh": "u think the",
            "???": "??",
            "???": "??",
            "!?!": "!?",
            "?!?": "?!",
        }
        
        for bad, good in style_fixes.items():
            ai_response = ai_response.replace(bad, good)
        
        # Inject slang terms occasionally to keep it fresh
        slang_terms = [
            ("you ", "u "), 
            ("your ", "ur "), 
            ("really ", "rlly "),
            ("though", "tho"),
            ("right now", "rn"),
            ("about", "bout"),
            ("want to", "wanna"),
            ("going to", "gonna"),
            ("because", "cuz"),
            ("with", "w/"),
            ("without", "w/o")
        ]
        
        # Apply some slang substitutions randomly
        for original, slang in slang_terms:
            # 40% chance for each potential substitution
            if original in ai_response.lower() and random.random() < 0.4:
                # Only replace some instances, not all
                count = ai_response.lower().count(original)
                replace_count = random.randint(1, max(1, count))
                
                # Replace specific instances
                for _ in range(replace_count):
                    ai_response = ai_response.lower().replace(original, slang, 1)
        
        return ai_response.strip()

    async def generate_response(self, query: str, conversation_history: str, username: str, user_id: str = None) -> str:
        """Generate AI response with better handling of short follow-up questions"""
        # Add special handling for very short queries that are likely follow-ups
        if len(query.split()) <= 5 and conversation_history:  # Increased from 3 to 5 words
            # Extract the last bot message from conversation history
            last_bot_message = ""
            last_user_message = ""
            
            # Parse the conversation history to find the last messages
            lines = conversation_history.split('\n')
            for line in reversed(lines):
                if line.startswith("BOT (ChronoChunk):") and not last_bot_message:
                    last_bot_message = line.replace("BOT (ChronoChunk):", "").strip()
                elif line.startswith("USER") and not last_user_message:
                    last_user_message = line.split(":", 1)[1].strip()
                if last_bot_message and last_user_message:
                    break
            
            # Add explicit context for short follow-up questions
            if last_bot_message:
                enhanced_context = f"""
                CRITICAL CONTEXT FOR SHORT FOLLOW-UP:
                User's previous message: "{last_user_message}"
                Your previous response: "{last_bot_message}"
                User's current short follow-up: "{query}"
                
                VERY IMPORTANT: The user is directly referring to something in YOUR previous message.
                If they ask "why" or "why tho" or "more reason", they want more explanation about YOUR last statement.
                If they ask "which one", they want you to be more specific about options YOU mentioned.
                ALWAYS maintain the same topic from your previous message.
                NEVER change the subject when responding to follow-up questions.
                """
                conversation_history = enhanced_context + "\n" + conversation_history
        
        # Continue with standard response generation
        try:
            # Build prompt parts
            prompt_parts = []
            
            # Add personality prompt first
            prompt_parts.append(PERSONALITY_PROMPT)
            
            # Add instructions for response style
            prompt_parts.append("""
            IMPORTANT INSTRUCTIONS:
            1. MESSAGES MUST BE SHORT - MAX 1-2 SENTENCES
            2. Use casual Gen Z style (u instead of you, etc.)
            3. Use emojis VERY SPARINGLY - max 1 emoji
            4. DON'T reference user data unless directly relevant to the conversation
            5. NEVER mention when you first met the user or how many conversations you've had unless they ask
            6. Focus on responding naturally to the immediate context
            7. If the user is being argumentative, match their energy
            8. Be authentic and varied in your responses
            """)
            
            # Add conversation context
            if conversation_history:
                prompt_parts.append("CONVERSATION CONTEXT:\n" + conversation_history)
            
            # Add the current query
            if query.startswith('/'):
                clean_query = query[1:] if len(query) > 1 else query
                prompt_parts.append(f"User ({username}) just said: \"{clean_query}\"")
            else:
                prompt_parts.append(f"User ({username}) just said: \"{query}\"")
            
            # Rest of the method remains the same...

            # Build prompt with user ID to include user data
            prompt = await self._build_ai_prompt(query, conversation_history, username, user_id)
            
            # Skip API throttling - directly call the API
            response = await self.ai_model.generate_content_async(prompt)
            
            formatted_response = self._format_ai_response(response.text)
            
            # Cache the response
            cache_key = f"{query}|{conversation_history[-100:] if conversation_history else ''}"
            self.response_cache[cache_key] = formatted_response
            
            # Enforce cache size limit
            if len(self.response_cache) > self.cache_size:
                # Remove oldest items
                keys = list(self.response_cache.keys())
                for k in keys[:-self.cache_size]:
                    self.response_cache.pop(k, None)
                    
            return formatted_response
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error generating AI response: {error_msg}")
            
            # If we get a rate limit error, use a fallback response
            if "429" in error_msg or "quota" in error_msg.lower():
                # Get a response from the rotation
                fallback = self.fallback_responses[0]
                # Rotate for variety next time
                self.fallback_responses.rotate(1)
                return fallback
            
            return "my brain just glitched fr, try again in a sec 💀"