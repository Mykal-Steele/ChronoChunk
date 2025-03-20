import asyncio
import random
import re
import logging
import google.generativeai as genai
from typing import List, Tuple, Dict, Any, Optional
from config.ai_config import PERSONALITY_PROMPT
import json
from collections import deque

# Setup logging
logger = logging.getLogger(__name__)

class AIResponseHandler:
    """Handles all AI response generation logic"""
    
    def __init__(self, api_key: str, important_topics: List[str], intent_detector, api_throttler=None):
        """Initialize AI response handler with API key and required components"""
        self.important_topics = important_topics
        self.intent_detector = intent_detector
        self.api_throttler = api_throttler
        
        # Response cache to avoid repeated API calls with similar prompts
        self.response_cache = {}
        self.cache_size = 50
        
        # Set up AI model
        genai.configure(api_key=api_key)
        self.ai_model = genai.GenerativeModel("gemini-1.5-flash-latest")
        
        # Fallback responses for when API is quota limited
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
    
    async def _build_ai_prompt(self, query: str, conversation_history: str, username: str) -> str:
        """Build the prompt for AI response generation"""
        # Check for argumentative intent
        is_argumentative, arg_type = await self.intent_detector.detect_argumentative_intent(query)
        
        # Check for sensitive topics
        important_topics = self.extract_important_topics(query)
        
        # Start building the prompt with personality and instructions first
        prompt_parts = []
        
        # Personality first so it colors everything else
        prompt_parts.append(PERSONALITY_PROMPT.replace("{query}", "").replace("{conversation_history}", ""))
        
        # Add additional instructions for clarity and context-awareness
        prompt_parts.append("""
        IMPORTANT INSTRUCTIONS:
        1. MESSAGES SHOULD VARY IN LENGTH - Use a mix of lengths from 2-5 sentences (never just 1 sentence)
        2. TYPING STYLE IS CRITICAL - type like a real Gen Z teen in Discord:
           - almost never use capital letters
           - rarely use periods at end of sentences
           - use "u" not "you", "ur" not "your", "n" not "and" consistently
           - drop apostrophes in contractions (dont, cant, wont)
           - make occasional typos like "teh" instead of "the"
           - DON'T put sentences on separate lines - keep as continuous text
           - use multiple question marks or exclamation marks (???)
        3. Use emojis VERY SPARINGLY - maximum 1 emoji per message
        4. Use a variety of slang expressions and casual language
        5. NEVER use proper grammar or formal writing style
        6. Don't end messages asking the same questions every time
        7. CRITICAL: Pay attention to who said what in the conversation history
        8. Remember everything that was previously discussed in the conversation
        9. Don't confuse what you said with what the user said
        10. Use shortened words like "u" instead of "you", "rn" instead of "right now"
        11. NEVER capitalize first words of sentences
        12. Be unpredictable and natural - vary your style slightly
        13. IMPORTANT: ONLY be ridiculous/nonsensical about 5% of the time, be more grounded and focused the rest of the time
        14. VARY MESSAGE LENGTH - sometimes use 2 sentences, sometimes use 3-5 sentences
        15. DON'T BE OVERLY DISMISSIVE - if the user asks a question, give an actual answer at least half the time
        16. DON'T overuse phrases like "wtf u care" or similar dismissive phrases
        17. Don't constantly talk about the same random topics (like time travel) in every message
        18. If the user seems confused or annoyed, tone down the nonsense and be more responsive
        19. STAY ON TOPIC - your responses should relate to what the user just said at least 85% of the time
        """)
        
        # Extra instruction for command-like messages
        if query.startswith('/'):
            command_style = """
            EXTRA IMPORTANT - FAILED COMMAND TYPING STYLE:
            - KEEP IT SHORT! 1-2 SENTENCES MAX, NEVER MORE THAN 3 SENTENCES TOTAL
            - DON'T split sentences onto separate lines - one continuous paragraph
            - respond EXTERME in casual style
            - NEVER capitalize anything, especially not first words of sentences
            - Drop all apostrophes (dont instead of don't, etc)
            - Use "u" not "you", "ur" not "your", "n" instead of "and" 
            - Use phrases like "bro", "fr", "ong", "no cap"naturally but not excessively
            - NEVER sound like a chatbot or assistant
            - Respond like a friend in a Discord chat, not a helper
            - Use shortened/abbreviated words throughout 
            - Drop unnecessary words that aren't needed
            - Use run-on sentences with almost no punctuation
            - Use multiple question marks (???) not just one
            - Use AT MOST one emoji in the entire message or none at all (can use emoticon as well but not more than one or none at all)
            - DON'T be overly dismissive - respond naturally to what they're asking (if they are being a bitch, you can ignore this just give them what they give you)
            - SOMETIMES be helpful/informative 
            
            EXAMPLE STYLE TO COPY:
            "bruh u fr tried that command lmaoo that shit dont even exist like what u even tryna do rn??? im confused asf"
            """
            prompt_parts.append(command_style)
        
        # Add argumentative instructions if needed
        if is_argumentative:
            # Random chance to argue back (40-50% of the time) for more natural feel
            if random.random() < 0.45:  
                arg_instructions = f"""
                ARGUMENT DETECTED - TYPE: {arg_type}
                The user is being argumentative or insulting. As a real friend would:
                - Match their energy but with personality
                - Defend yourself with wit and bite
                - Don't back down - throw it back at them
                - Use comebacks appropriate to their level of intensity
                - Include casual insults like "clown", "bro you trippin", "get outta here with that"
                - Call them out on their BS
                - If they insult you, insult them back but cleverly
                - Be quick-witted and sharp with the comebacks
                - No need to be polite - be real and authentic
                - Do NOT apologize for having an opinion
                - Occasionally use their own logic against them
                - BUT YOU GOTTA GO BACK TO NORMAL IF YOU FORGIVE THEM which could be like 60% forgivness, 40% is just waiting for that 60% to hit and forgive. but still be bitchy tho and in a realicsic way to forgive them like u would in a real conversation
                """
                prompt_parts.append(arg_instructions)
        
        # Add sensitive topic warnings if needed
        if important_topics:
            sensitive_instructions = f"""
            ATTENTION: This message contains sensitive topics: {', '.join(important_topics)}
            - Be mindful of these topics in your response
            - Don't make jokes about these topics (light one or personalized one are fine to lighten the mood)
            - Be supportive but not condescending
            """
            prompt_parts.append(sensitive_instructions)
        
        # If user seems annoyed (uses words like "stfu", "stop", etc.)
        if any(word in query.lower() for word in ["stfu", "stop", "stawp", "shut up", "stop it"]):
            prompt_parts.append("""
            IMPORTANT: The user seems annoyed. Change the subject completely.
            - Briefly acknowledge and move on to something new
            - Don't be defensive or argue back
            - Be chill and genuine, not sarcastic
            - Ask about something completely different or offer a genuinely interesting topic
            """)
        
        # Add conversation history AFTER instructions but BEFORE the query
        if conversation_history:
            prompt_parts.append("CONVERSATION CONTEXT:\n" + conversation_history)
        
        # Add user query with name as the LAST element
        if query.startswith('/'):
            # If it was an unrecognized command, remove the slash for natural chat
            clean_query = query[1:] if len(query) > 1 else query
            prompt_parts.append(f"User ({username}) just said: \"{clean_query}\"")

        else:
            prompt_parts.append(f"User ({username}) just said: \"{query}\"")
        
        # Ask AI to respond with style reminders
        prompt_parts.append("""
        REMEMBER:
        - VARY YOUR RESPONSES! Sometimes be chill, sometimes excited, sometimes confused - be unpredictable
        - DON'T use the same phrases all the time (never repeat "rizz is undefeated" or similar phrases)
        - VARY YOUR SENTENCE LENGTH! Sometimes super short, sometimes a couple sentences
        - DON'T sound like you're following a template or formula
        - Be less defensive when challenged - sometimes just laugh it off or agree
        - Don't try to "win" every argument - real people sometimes concede points
        - If someone seems annoyed with you, sometimes just back off instead of doubling down
        - Don't mention "touching grass" in every other message
        - When confused, ask genuinely confused questions instead of being hostile
        - Roll with whatever topic the user brings up rather than fixating on old topics
        - VARY MESSAGE LENGTH! Use 2-5 sentences (sometimes shorter, sometimes longer)
        - DON'T put sentences on separate lines - keep everything in one continuous paragraph
        - almost NEVER use capital letters
        - use "u" not "you", "ur" not "your", etc. 
        - rarely use periods at end of sentences
        - drop apostrophes (dont, wont, cant)
        - use emojis VERY SPARINGLY (only 1 emoji max if any)
        - use multiple question/exclamation marks for emphasis
        - Be natural and unpredictable like a real person would
        - ACTUALLY RESPOND TO WHAT THE USER SAID - stay on topic!
        - When user asks a question, give a real answer at least half the time
        - Don't be overly dismissive or use "wtf u care" type phrases too much
        - Vary your topics instead of repeating the same themes (like time travel)
        - Avoid overly random tangents and hallucinations
        """.strip())
        
        prompt_parts.append("""
        EMOJI RULES:
        - When you want to use an emoji like :KEKW: or :pokeBruh:, just write :KEKW: or :pokeBruh:
        - NEVER include emoji IDs like <:KEKW:1280462195939082242>
        - NEVER copy the exact emoji format from the user - rewrite it naturally
        - If youo want to use the emoji :KEKW:, respond with :KEKW: not with <:KEKW:1280462195939082242>
        """)
        
        # Randomly adjust how bizarre the bot will be for this message
        nonsense_factor = random.random()
        if nonsense_factor < 0.65:  # 65% chance to be coherent
            prompt_parts.append("SPECIAL INSTRUCTION: Be very coherent and logical in this response. No nonsense.")
        elif nonsense_factor > 0.90:  # Only 10% chance to be weird
            prompt_parts.append("SPECIAL INSTRUCTION: Be slightly bizarre in this response. Add an unexpected twist.")
        
        # Combine all parts
        return "\n\n".join(prompt_parts)
        
    def _format_ai_response(self, raw_response: str) -> str:
        """Format and clean up the AI response for better readability"""
        if not raw_response:
            return "..."
            
        # Extract the actual response text
        ai_response = raw_response.strip()
        
        # Strip off prefixes if returned by the model
        ai_response = re.sub(r'^(You:|Your response as ChronoChunk:|ChronoChunk:)\s*', '', ai_response)
        
        # Fix newlines - ensure there are no consecutive empty lines
        ai_response = re.sub(r'\n\s*\n', '\n', ai_response)
        
        # Replace all newlines between sentences with spaces
        ai_response = re.sub(r'([.!?])\s*\n', r'\1 ', ai_response)
        
        # Fix multiple spaces in a row
        ai_response = re.sub(r' +', ' ', ai_response)
        
        # Allow longer responses (up to 5 sentences)
        sentences = re.split(r'(?<=[.!?])\s+', ai_response)
        if len(sentences) > 5:
            ai_response = ' '.join(sentences[:5])
        
        # Reduce emoji usage (if more than one emoji, keep only one random emoji)
        emoji_pattern = re.compile(r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F700-\U0001F77F\U0001F780-\U0001F7FF\U0001F800-\U0001F8FF\U0001F900-\U0001F9FF\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF\U00002702-\U000027B0\U000024C2-\U0001F251]+')
        emojis = emoji_pattern.findall(ai_response)
        if len(emojis) > 1:
            # Keep only one random emoji from those found
            emoji_to_keep = random.choice(emojis)
            for emoji in emojis:
                if emoji != emoji_to_keep:
                    ai_response = ai_response.replace(emoji, '', 1)
        
        return ai_response

    async def generate_response(self, query: str, conversation_history: str, username: str) -> str:
        """Generate an AI response to a user query"""
        # Use cache for identical queries with same context to reduce API calls
        cache_key = f"{query}|{conversation_history[-100:] if conversation_history else ''}"
        if cache_key in self.response_cache:
            return self.response_cache[cache_key]
            
        try:
            # Use API throttler if available
            acquired = False
            if self.api_throttler:
                await self.api_throttler.acquire()
                acquired = True
                
            # Build the prompt
            prompt = await self._build_ai_prompt(query, conversation_history, username)
            
            # Call the AI model with slightly lower temperature
            response = await self.ai_model.generate_content_async(
                prompt,
                generation_config={
                    "temperature": 0.75,  # Slightly reduced from default
                    "top_p": 0.85,
                    "max_output_tokens": 200  # Reasonable length for Discord
                }
            )
            
            # Format the response
            result = self._format_ai_response(response.text)
            
            # Cache the response
            if len(self.response_cache) >= self.cache_size:
                # Remove oldest item
                self.response_cache.pop(next(iter(self.response_cache)))
            self.response_cache[cache_key] = result
            
            return result
            
        except Exception as e:
            logger.error(f"Error generating AI response: {e}")
            
            # Get a fallback response when quota is exhausted
            if "429" in str(e):
                fallback = self.fallback_responses[0]
                # Rotate the queue to get different responses next time
                self.fallback_responses.rotate(1)
                return fallback
            return "damn, my brain just glitched for a sec... gimme a min"
        finally:
            # Release the throttler if we acquired it
            if self.api_throttler and acquired:
                await self.api_throttler.release()