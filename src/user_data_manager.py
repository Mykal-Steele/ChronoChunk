import json
import os
import re
import time
import logging
from datetime import datetime
import google.generativeai as genai
from typing import Dict, List, Any, Optional, Tuple
from config.config import Config
from config.ai_config import FACT_EXTRACTION_PROMPT, TOPIC_EXTRACTION_PROMPT, CONTRADICTION_CHECK_PROMPT, CORRECTION_PROMPT, PERSPECTIVE_CONVERSION_PROMPT

# Setup logger 
logger = logging.getLogger(__name__)

class UserDataManager:
    """Manages persistent user data storage and retrieval"""
    
    def __init__(self, data_dir=None, model=None):
        """Initialize the user data manager"""
        # Set data directory
        self.data_dir = data_dir or os.path.join(os.path.dirname(os.path.dirname(__file__)), "user_data")
        os.makedirs(self.data_dir, exist_ok=True)
        
        # Initialize the fact extraction model
        try:
            # Add this import inside the method to avoid circular imports
            import google.generativeai as genai
            from config.config import Config
            
            api_key = os.environ.get("GEMINI_API_KEY") or Config.GEMINI_API_KEY
            genai.configure(api_key=api_key)
            self.fact_model = genai.GenerativeModel("gemini-1.5-flash-latest")
            logger.info("Fact extraction model initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize fact extraction model: {e}")
            self.fact_model = None
        
        logger.info(f"UserDataManager initialized with data directory: {self.data_dir}")
        
    def _get_user_file_path(self, user_id: str) -> str:
        """Get file path for user data - using sanitized user ID"""
        # Ensure we're only using the numeric part of the Discord ID
        # Discord IDs are always numeric and unique per user
        user_id = re.sub(r'[^0-9]', '', user_id)
        return os.path.join(self.data_dir, f"{user_id}.json")

    def load_user_data(self, user_id: str, username: str = None) -> Dict[str, Any]:
        """Load user data with consistent username handling"""
        # Create file path
        file_path = self._get_user_file_path(user_id)
        
        # Create default data structure
        default_data = {
            "user_id": user_id,
            "username": username,  # Always include username
            "created_at": datetime.now().isoformat(),
            "personal_info": {},
            "preferences": {},
            "facts": [],
            "topics_of_interest": [],
            "conversation_history": [],
            "last_interaction": datetime.now().isoformat()
        }
        
        try:
            # Create user file if it doesn't exist
            if not os.path.exists(file_path):
                with open(file_path, 'w', encoding='utf-8') as f:  # Add UTF-8 encoding here
                    json.dump(default_data, f, indent=2, ensure_ascii=False)
                return default_data
                
            # Read existing file - ADD ENCODING='UTF-8' HERE
            with open(file_path, 'r', encoding='utf-8') as f:  # This is the critical fix
                user_data = json.load(f)
                
            # Always update username if provided
            if username and user_data.get("username") != username:
                user_data["username"] = username
                self.save_user_data(user_id, user_data)
                
            return user_data
            
        except Exception as e:
            logger.error(f"Error loading user data: {e}")
            return default_data

    def save_user_data(self, user_id: str, data: Dict[str, Any]) -> None:
        """Save user data to file"""
        # Sanitize Discord user ID for consistency 
        user_id = re.sub(r'[^0-9]', '', user_id)
        
        # Update last interaction time
        data["last_interaction"] = datetime.now().isoformat()
        
        file_path = self._get_user_file_path(user_id)
        try:
            # Ensure the directory exists
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                logging.info(f"Saved user data for {user_id}")
        except Exception as e:
            logging.error(f"Failed to save user data for {user_id}: {e}")
            
    async def add_conversation(self, user_id: str, message_content: str, bot_response: str, username: str = None) -> None:
        """Add conversation to user data"""
        # Sanitize Discord user ID
        user_id = re.sub(r'[^0-9]', '', user_id)
        user_data = self.load_user_data(user_id, username)
        
        # Add conversation to history
        user_data["conversation_history"].append({
            "timestamp": datetime.now().isoformat(),
            "user_message": message_content,
            "bot_response": bot_response
        })
        
        # Increment total conversation counter (add this field if it doesn't exist)
        if "total_conversations" not in user_data:
            user_data["total_conversations"] = len(user_data["conversation_history"])
        else:
            user_data["total_conversations"] += 1
        
        # Keep only the configured number of conversations
        if len(user_data["conversation_history"]) > Config.MAX_CONVERSATION_HISTORY:
            user_data["conversation_history"] = user_data["conversation_history"][-Config.MAX_CONVERSATION_HISTORY:]
        
        # Save the updated data
        self.save_user_data(user_id, user_data)
        
        # Also extract facts from this conversation
        await self.extract_and_save_facts(user_id, message_content, username)
        
    async def extract_and_save_facts(self, user_id: str, message_content: str, username: str = None) -> bool:
        """Extract and save facts about the user using AI"""
        # CHANGE THIS CHECK to handle more cases:
        original_content = message_content
        
        # Remove slash prefix if present (for /hello style messages)
        if message_content.startswith('/') and len(message_content) > 1:
            message_content = message_content[1:].strip()
        
        # Skip fact extraction for very short messages or commands
        if not message_content or len(message_content.split()) < 2:
            return False
        
        # Skip actual commands like "/chat" or "/help"
        if message_content.split()[0].lower() in ["chat", "code", "help", "summary", "profile", "stats"]:
            return False
            
        user_data = self.load_user_data(user_id, username)
        made_changes = False
        
        try:
            # IMPROVED fact extraction prompt
            fact_prompt = """
            Extract ONLY definite personal facts about the user from this message.
            Respond with ONLY a JSON array of facts in second-person format.
            
            Example message: "I am 21 years old and I hate math"
            Example response: ["You are 21 years old", "You hate math"]
            
            Example message: "I love coffee so much"
            Example response: ["You love coffee"]
            
            Example message: "My cat just died"
            Example response: ["Your cat died"]
            
            Example message: "I hate Monday"
            Example response: ["You hate Monday"]
            
            Example message: "My friend is a doctor"
            Example response: ["Your friend is a doctor"] 
            
            Example message: "I don't eat meat anymore"
            Example response: ["You do not eat meat"]
            
            IMPORTANT RULES:
            - Include any personal facts (preferences, status, relationships, possessions)
            - Use second-person format ("You are", "You have", "You like", "Your friend is")
            - Return as a plain JSON array ["fact 1", "fact 2"]
            - Return empty array [] if no clear facts present
            - DO NOT miss obvious facts - especially likes, dislikes, and relationships
            
            USER MESSAGE: """ + message_content
            
            # Extract facts
            response = await self.fact_model.generate_content_async(fact_prompt)
            facts_text = response.text.strip()
            
            # Handle markdown formatting
            if "```json" in facts_text:
                facts_text = facts_text.split("```json")[1].split("```")[0].strip()
            elif "```" in facts_text:
                facts_text = facts_text.split("```")[1].strip()
                
            # Parse facts from response
            try:
                facts_object = json.loads(facts_text)
                
                # Check for conflicting facts before adding new ones
                for fact in facts_object:
                    if fact and len(fact.strip()) > 0:
                        # Add as new fact if not exists
                        if not self._fact_exists(user_data, fact):
                            user_data["facts"].append({
                                "content": fact,    
                                "extracted_from": message_content,
                                "timestamp": datetime.now().isoformat()
                            })
                            made_changes = True
                            
            except json.JSONDecodeError:
                # Silent fail on JSON errors - likely API quota issues
                pass
                
        except Exception as e:
            # API quota exceeded or other error - fail silently
            if "429" in str(e):
                # Quota error, just return without logging spam
                return False
                
        # Save changes if we made any
        if made_changes:
            self.save_user_data(user_id, user_data)
            
        return made_changes
    
    def _clean_topic(self, topic: str) -> str:
        """Clean up a topic by removing qualifiers and extra words"""
        if not topic:
            return ""
            
        # Use the more robust _clean_topics method and return the first result
        cleaned_topics = self._clean_topics([topic])
        if cleaned_topics:
            return cleaned_topics[0]
            
        # Fallback to the original implementation
        # Combine regex operations for better performance
        qualifiers = r'\b(so much|really|very|a lot|kinda|sort of|type of|kind of|totally|absolutely|definitely|quite|extremely|somewhat)\b'
        
        # Convert to lowercase, remove qualifiers, and clean up whitespace in a single pass
        clean = re.sub(r'\s+', ' ', re.sub(qualifiers, '', topic.lower())).strip()
        
        # Use a direct lookup for singularization (faster than multiple conditions)
        singulars = {
            "burgers": "burger",
            "cats": "cat", 
            "dogs": "dog",
            "games": "gaming",
            "stocks": "investing",
            "investments": "investing",
            # Keep these as-is
            "movies": "movies",
            "books": "books",
        }
        
        return singulars.get(clean, clean)
    
    async def _check_and_handle_contradiction(self, user_id: str, user_data: Dict[str, Any], new_fact: str) -> bool:
        """Check if a new fact contradicts existing facts and handle appropriately"""
        if not new_fact or not user_data["facts"]:
            return False
        
        # Sanitize Discord user ID
        user_id = re.sub(r'[^0-9]', '', user_id)
        
        existing_facts = [f["content"] if isinstance(f, dict) and "content" in f else f for f in user_data["facts"]]
        contradictions_prompt = CONTRADICTION_CHECK_PROMPT.format(
            existing_facts=json.dumps(existing_facts),
            new_fact=new_fact
        )
        
        try:
            response = await self.fact_model.generate_content_async(contradictions_prompt)
            result_text = response.text.strip()
            
            # Handle markdown formatting
            if "```json" in result_text:
                result_text = result_text.split("```json")[1].split("```")[0].strip()
            elif "```" in result_text:
                result_text = result_text.split("```")[1].strip()
                
            result = json.loads(result_text)
            
            if result["contradicts"] and result["fact_index"] >= 0:
                # Handle contradiction based on action
                if result.get("action") == "replace":
                    # Replace old fact with new one
                    if isinstance(user_data["facts"][result["fact_index"]], dict):
                        user_data["facts"][result["fact_index"]]["content"] = new_fact
                        user_data["facts"][result["fact_index"]]["updated_at"] = datetime.now().isoformat()
                    else:
                        user_data["facts"][result["fact_index"]] = {
                            "content": new_fact,
                            "updated_at": datetime.now().isoformat()
                        }
                    logging.info(f"Replaced contradicting fact for {user_id}")
                    self.save_user_data(user_id, user_data)
                    return True
                    
                elif result.get("action") == "delete_old":
                    # Delete the old fact
                    user_data["facts"].pop(result["fact_index"])
                    logging.info(f"Removed contradicting fact for {user_id}")
                    self.save_user_data(user_id, user_data)
                    return False  # Allow the new fact to be added
                    
                elif result.get("action") == "ignore_new":
                    # Ignore the new fact
                    logging.info(f"Ignored new contradicting fact for {user_id}")
                    return True
                    
                # For "keep_both", we don't need to do anything special
            
            return False
        except Exception as e:
            logging.error(f"Error checking contradictions: {e}")
            return False
    
    def _fact_exists(self, user_data: dict, new_fact: str) -> bool:
        """Check if fact already exists or conflicts with existing facts"""
        if not user_data or "facts" not in user_data:
            return False
            
        # Convert to lowercase for comparison
        new_fact_lower = new_fact.lower()
        
        # Extract key terms for better matching
        # For example "You are 21 years old" -> "age"
        fact_topics = {
            "age": ["you are", "years old", "age"],
            "location": ["you live in", "you are from", "you moved to"],
            "preference": ["you like", "you love", "you hate", "you don't like", "you enjoy"],
            "status": ["you have", "you own", "you got", "you bought"]
        }
        
        # Determine topic of new fact
        fact_topic = None
        for topic, keywords in fact_topics.items():
            if any(keyword in new_fact_lower for keyword in keywords):
                fact_topic = topic
                break
        
        # Check for conflicts
        for existing_fact in user_data["facts"]:
            # Get fact content
            if isinstance(existing_fact, dict) and "content" in existing_fact:
                content = existing_fact["content"].lower()
            else:
                content = str(existing_fact).lower()
                
            # Exact match
            if content == new_fact_lower:
                return True
                
            # Check for topic conflicts (if we identified a topic)
            if fact_topic:
                fact_matches_topic = any(keyword in content for keyword in fact_topics.get(fact_topic, []))
                
                # If same topic but different content, we have a contradiction
                if fact_matches_topic:
                    # Remove the conflicting fact
                    if isinstance(existing_fact, dict):
                        user_data["facts"].remove(existing_fact)
                    else:
                        user_data["facts"].remove(existing_fact)
                    return False  # Return False to add the new fact
        
        return False
            
    async def handle_correction(self, user_id: str, correction_message: str, username: str = None) -> bool:
        """Handle user correction of stored facts"""
        # Sanitize Discord user ID
        user_id = re.sub(r'[^0-9]', '', user_id)
        user_data = self.load_user_data(user_id, username)
        
        if not user_data["facts"]:
            return False
        
        try:
            # Create list of facts for the AI to analyze
            facts_list = "\n".join([
                f"{i+1}. {fact['content'] if isinstance(fact, dict) and 'content' in fact else fact}"
                for i, fact in enumerate(user_data["facts"])
            ])
            
            correction_prompt = CORRECTION_PROMPT.format(
                facts_list=facts_list,
                correction_message=correction_message
            )
            
            response = await self.fact_model.generate_content_async(correction_prompt)
            correction_text = response.text.strip()
            
            # Handle markdown formatting
            if "```json" in correction_text:
                correction_text = correction_text.split("```json")[1].split("```")[0].strip()
            elif "```" in correction_text:
                correction_text = correction_text.split("```")[1].strip()
            
            # AI responses are unpredictable - we need to extract the actual JSON
            # from whatever format the model decided to return it in
            # sometimes they add explanations or wrap in markdown, so we need to clean it up
            try:
                correction_data = json.loads(correction_text)
                
                if correction_data.get("action") == "delete":
                    if 0 <= correction_data.get("fact_index", -1) < len(user_data["facts"]):
                        removed_fact = user_data["facts"].pop(correction_data["fact_index"])
                        fact_content = removed_fact["content"] if isinstance(removed_fact, dict) and "content" in removed_fact else removed_fact
                        logging.info(f"Removed fact for {user_id}: {fact_content}")
                        self.save_user_data(user_id, user_data)
                        return True
                        
                elif correction_data.get("action") == "update":
                    if 0 <= correction_data.get("fact_index", -1) < len(user_data["facts"]) and "new_fact" in correction_data:
                        if isinstance(user_data["facts"][correction_data["fact_index"]], dict) and "content" in user_data["facts"][correction_data["fact_index"]]:
                            old_fact = user_data["facts"][correction_data["fact_index"]]["content"]
                            user_data["facts"][correction_data["fact_index"]]["content"] = correction_data["new_fact"]
                            user_data["facts"][correction_data["fact_index"]]["updated_at"] = datetime.now().isoformat()
                        else:
                            old_fact = user_data["facts"][correction_data["fact_index"]]
                            user_data["facts"][correction_data["fact_index"]] = {
                                "content": correction_data["new_fact"],
                                "updated_at": datetime.now().isoformat()
                            }
                        
                        logging.info(f"Updated fact for {user_id}: '{old_fact}' to '{correction_data['new_fact']}'")
                        self.save_user_data(user_id, user_data)
                        return True
                        
            except json.JSONDecodeError as e:
                logging.warning(f"Failed to parse correction JSON: {correction_text} - Error: {e}")
                
        except Exception as e:
            logging.error(f"Error handling correction: {e}")
            
        return False
    
    def remove_fact(self, user_id: str, fact_to_remove: str, username: str = None) -> bool:
        """Remove a specific fact from user data"""
        if not fact_to_remove:
            return False
        
        # Sanitize Discord user ID
        user_id = re.sub(r'[^0-9]', '', user_id)    
        user_data = self.load_user_data(user_id, username)
        
        original_length = len(user_data["facts"])
        
        # Handle both dictionary and string formats
        filtered_facts = []
        for fact in user_data["facts"]:
            content = fact["content"] if isinstance(fact, dict) and "content" in fact else fact
            if fact_to_remove.lower() not in content.lower():
                filtered_facts.append(fact)
                
        user_data["facts"] = filtered_facts
        
        if len(user_data["facts"]) != original_length:
            self.save_user_data(user_id, user_data)
            return True
            
        return False
        
    def get_user_summary(self, user_id: str, username: str = None) -> str:
        """Get user summary with facts and interests but NOT mentioning conversation metadata"""
        user_data = self.load_user_data(user_id, username)
        summary_parts = []
        
        # Add facts but NOT conversation counts or dates
        if "facts" in user_data and user_data["facts"]:
            formatted_facts = []
            for fact in user_data["facts"]:
                content = fact.get("content", "")
                if content:
                    formatted_facts.append(f"- {content}")
            
            if formatted_facts:
                summary_parts.append("Things I know about you:")
                summary_parts.extend(formatted_facts)
        
        # Add topics of interest if available
        if "topics_of_interest" in user_data and user_data["topics_of_interest"]:
            topics = user_data["topics_of_interest"]
            if topics:
                summary_parts.append("\nTopics you're interested in:")
                summary_parts.append(", ".join(topics))
        
        # IMPORTANT: Do NOT include conversation counts or dates
        
        # If we have no info, mention that
        if not summary_parts:
            return "I don't have any information about you yet."
        
        return "\n".join(summary_parts)

    def _clean_topics(self, topics: List[str]) -> List[str]:
        """Clean the list of topics to ensure they are valid.
        
        Args:
            topics: A list of topic strings to clean.
            
        Returns:
            A list of cleaned topics.
        """
        if not topics:
            return []
        
        # protect against model hallucination and potential prompt injection
        # models sometimes make up weird topics or try to add instructions as topics
        # we need to be aggressive with filtering to avoid storing bad data
        cleaned = []
        for topic in topics:
            # lowercase and strip whitespace to normalize
            topic = topic.lower().strip()
            
            # remove any non-alphanumeric chars except spaces
            # this prevents sneaky injection attempts with special chars
            topic = re.sub(r'[^a-z0-9\s]', '', topic)
            
            # skip topics that are too short/long or empty after cleaning
            # most valid topics are 2-30 chars - outside that is suspicious
            if topic and 2 <= len(topic) <= 30:
                cleaned.append(topic)
                
        return cleaned

    def _sanitize_discord_id(self, user_id):
        """Sanitize Discord user ID to ensure it's valid for filenames"""
        # Remove any non-numeric characters
        sanitized = ''.join(c for c in user_id if c.isdigit())
        return sanitized

    def get_conversation_stats(self, user_id: str) -> Dict[str, Any]:
        """Get conversation statistics including total message count and first interaction date"""
        user_data = self.load_user_data(user_id)
        
        # Use the dedicated counter instead of just counting the history length
        total_conversations = user_data.get("total_conversations", len(user_data.get("conversation_history", [])))
        
        # Get first interaction timestamp
        first_interaction = None
        if user_data.get("conversation_history", []):
            try:
                first_timestamp = user_data["conversation_history"][0]["timestamp"]
                first_interaction = first_timestamp.split("T")[0]  # Just the date part
            except (KeyError, IndexError):
                pass
        
        return {
            "total_messages": total_conversations,
            "first_interaction": first_interaction
        }