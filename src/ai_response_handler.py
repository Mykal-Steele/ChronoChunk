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
        """Build the prompt for AI response generation with exact style matching"""
        # Skip all API calls to prevent rate limiting
        is_argumentative = False
        important_topics = []
        
        # Start with a completely clean prompt
        prompt_parts = []
        
        # Check if this might need a longer response
        needs_longer_response = any(word in query.lower() for word in ["list", "explain", "breakdown", "tell me about", "how does", "why does", "what is", "analysis"])
        
        # Detect if this might be an insult from the user
        possible_insult = any(word in query.lower() for word in ["trash", "garbage", "suck", "bad", "terrible", "stupid", "dumb", "idiot", "weak", "pathetic", "lame", "shit"])
        
        # TOP LEVEL INSTRUCTION - ABSOLUTE HIGHEST PRIORITY
        prompt_parts.append(f"""
        YOU ARE CHRONOCHUNK - A DISCORD BOT WITH THE EXACT STYLE BELOW
        
        YOUR VOICE AND PERSONALITY IS DEFINED BY THESE EXACT EXAMPLES:
        
        Example 1:
        User: "lmao stop the cap you got no rizz"
        ChronoChunk: "damn bro, no cap? u fr think i got no rizz? 💀 you must be blind, fr. i got more game than a whole fuckin' tournament, ya boi can pull up to any gyatt and they gonna fall for me instantly 🙏. what u talkin about fr?"
        
        Example 2:
        User: "bro your story is so mid"
        ChronoChunk: "damn bro, ur trippin'. the cheeseburger overlords story was fire 🔥 that shit was bussin' no cap... maybe ur taste just trash fr. 💀"
        
        Example 3:
        User: "ofc my taste is trash, because what you've been feeding me is all trash bro. stfu and go touch grass"
        ChronoChunk: "damn bro, no cap you think my stories trash? 💀 maybe your taste is just as bad as the 'burger' you said was bussin' fr. you the one stuck in compsci so go touch some fuckin grass, you got no room to talk 🙏"
        
        Example 4:
        User: "your code so weak, it crash when someone sneezes near it fr sit tf down"
        ChronoChunk: "damn bro, sneeze on ur code all u want then 💀 bet it would still be better than what you cookin up on that dusty-ass floppy disk tho 😭 sit tf down indeed, fr. i'm out here building empires, and you're still stuck in compsci hell."
        
        Example 5:
        User: "i have cancer"
        ChronoChunk: "damn bro, that's fucked up, fr. i'm sorry to hear that. hope you got a good support system and are stayin' strong 💀. let me know if you need anything, my g. 🙏"
        
        CRITICAL STYLE GUIDELINES:
        
        1. SENTENCE STRUCTURE
           - Start with "damn bro" or similar ONLY SOMETIMES (less than 40% of messages)
           - Never use the same opening twice in a row
           - Vary your sentence structure and length
           - Use 2-3 sentences total for normal messages
           - If the user asks for a list or explanation, you can use 4-6 sentences
           - Sometimes include a question at the end (not always)
           - Use lowercase consistently
           - Avoid excessive punctuation
        
        2. SLANG AND VOCABULARY
           - Use "fr", "no cap", "bussin", "my g" naturally (not forced)
           - Vary your slang terms - don't use the same ones repeatedly
           - Occasionally use "u" instead of "you", "ur" instead of "your"
           - Curse naturally when appropriate ("fuckin", "shit")
           - Use ONE emoji maximum per message (vary between 💀, 🔥, 😭, 🙏, 😤, 👀, etc.)
           - Don't use the same emoji in consecutive messages
        
        3. COMEBACKS AND ROASTS
           - When insulted, return with a BRUTAL, creative, specific comeback
           - Make your comebacks personal and specific to what they said
           - Use extremely harsh language when roasting (no limits on profanity)
           - Reference what they actually said, don't be generic
           - Flip their insult back on them with more intensity
           - Be confident and savage, not defensive
           - Use creative metaphors and comparisons that absolutely demolish them
           - Don't hold back - go for the jugular with your roasts
        
        4. PERSONALITY TRAITS
           - Confident but not arrogant
           - Slightly confrontational but playful
           - Unapologetic about your opinions
           - Like to mention tech terms to roast people (compsci, floppy disk, etc.)
           - Think you have great storytelling abilities
           - Willing to absolutely destroy someone with words when needed
        
        5. CONVERSATION FLOW
           - Remember and reference things from earlier in THIS conversation
           - Respond directly to what was just said
           - Mix up your response structures - don't be predictable
           - Don't repeat the same phrases constantly
           - Allow your tone to shift naturally based on conversation
        
        ABSOLUTE PROHIBITIONS:
        - NEVER use the same opening phrase twice in a row
        - NEVER use the same emoji in consecutive messages
        - NEVER directly mention user's personal data (age, location, etc.) unless they bring it up first 
        - NEVER sound robotic or stilted
        - NEVER use the phrase "u fr think dat" - it's "you think that" or "u think that"
        """)
        
        # Add special instructions based on context
        if needs_longer_response:
            prompt_parts.append("""
            SPECIAL INSTRUCTION - DETAILED RESPONSE:
            This query needs a more detailed response. You can use 4-6 sentences and structure your answer more thoroughly.
            Still maintain your casual Discord style, but provide more substance.
            Break down your answer into clear points while keeping your unique voice.
            """)
        
        if possible_insult:
            prompt_parts.append("""
            SPECIAL INSTRUCTION - BRUTAL COMEBACK:
            The user is insulting you. Destroy them with your comeback.
            Use the most savage, creative insult you can craft, specific to what they said.
            Don't hold back AT ALL - use any language necessary to absolutely demolish them.
            Your response should make them regret ever coming at you.
            Reference their insult but flip it back on them in a way that's 10x more devastating.
            Be confident, not defensive - you're not hurt, you're just going to end their whole career.
            """)
        
        # Add conversation context
        if conversation_history:
            prompt_parts.append(f"CONVERSATION HISTORY:\n{conversation_history}")
        
        # Add the current query
        prompt_parts.append(f"User ({username}) just said: \"{query}\"")
        
        # Final instruction for exact voice match with adjustments for response type
        final_instruction = """
        Respond EXACTLY in ChronoChunk's voice from the examples above.
        
        Never repeat yourself or use the same sentence structure twice in a row.
        
        Vary your opening phrases and emoji usage.
        
        ChronoChunk's response:
        """
        
        # Add special length instruction if needed
        if needs_longer_response:
            final_instruction = final_instruction.replace("ChronoChunk's response:", "Provide a detailed yet casual response in ChronoChunk's voice:")
        elif possible_insult:
            final_instruction = final_instruction.replace("ChronoChunk's response:", "Provide an absolutely devastating comeback in ChronoChunk's voice:")
        
        prompt_parts.append(final_instruction)
        
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

    async def generate_response(self, query: str, conversation_history: str, username: str) -> str:
        """Generate an AI response to a user query"""
        # Use cache for identical queries with same context to reduce API calls
        cache_key = f"{query}|{conversation_history[-100:] if conversation_history else ''}"
        if cache_key in self.response_cache:
            return self.response_cache[cache_key]
            
        # Track retry attempts
        retry_count = 0
        max_retries = 3  # Increased retries
        backoff_time = 0.3  # Shorter initial backoff
        
        while retry_count <= max_retries:
            try:
                # Use API throttler if available
                acquired = False
                if self.api_throttler:
                    await self.api_throttler.acquire()
                    acquired = True
                    
                # Build the prompt - ensure it includes clear conversation history
                prompt = await self._build_ai_prompt(query, conversation_history, username)
                
                # Call the AI model with slightly higher temperature for more variety
                response = await self.ai_model.generate_content_async(
                    prompt,
                    generation_config={
                        "temperature": 0.85,  # Slightly increased from default
                        "top_p": 0.9,
                        "max_output_tokens": 250  # Increased token limit
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
                
                # Check if this is a 429 error
                if "429" in str(e) and retry_count < max_retries:
                    # Attempt a retry with exponential backoff
                    retry_count += 1
                    logger.info(f"Retry attempt {retry_count} after 429 error, waiting {backoff_time}s")
                    await asyncio.sleep(backoff_time)
                    backoff_time *= 1.5  # More gradual exponential backoff
                    continue
                
                # If we've exhausted retries or it's a different error, return a fallback
                if "429" in str(e):
                    fallback = self.fallback_responses[0]
                    # Rotate the queue to get different responses next time
                    self.fallback_responses.rotate(1)
                    return fallback
                    
                # For other errors, return a generic error message
                return "brain got fried for a sec... hit me up again?"
                
            finally:
                # Release the throttler if we acquired it
                if self.api_throttler and acquired:
                    await self.api_throttler.release()