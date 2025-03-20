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
        # Use the data directory from Config if not specified
        self.data_dir = data_dir or Config.USER_DATA_DIR
        
        # Create the data directory if it doesn't exist
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)
            logging.info(f"Created user data directory: {self.data_dir}")
            
        # Use provided model or create our own using configured model
        self.fact_model = model or genai.GenerativeModel(Config.FACT_MODEL)
        
    def _get_user_file_path(self, user_id: str) -> str:
        """Get file path for user data - using sanitized user ID"""
        # Ensure we're only using the numeric part of the Discord ID
        # Discord IDs are always numeric and unique per user
        user_id = re.sub(r'[^0-9]', '', user_id)
        return os.path.join(self.data_dir, f"{user_id}.json")

    def load_user_data(self, user_id: str, username: str = None) -> Dict[str, Any]:
        """Load user data or create new profile"""
        # Sanitize Discord user ID for consistency
        user_id = re.sub(r'[^0-9]', '', user_id)
        file_path = self._get_user_file_path(user_id)
        
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Ensure all required fields are present
                    if "facts" not in data:
                        data["facts"] = []
                    if "topics_of_interest" not in data:
                        data["topics_of_interest"] = []
                    if "conversation_history" not in data:
                        data["conversation_history"] = []
                    if "username" not in data and username:
                        data["username"] = username
                    elif username and data.get("username") != username:
                        # Update username if it changed
                        data["username"] = username
                        self.save_user_data(user_id, data)
                    return data
            except json.JSONDecodeError as e:
                logging.warning(f"Corrupted user data for {user_id}: {e}")
        
        # Create new default user profile
        new_profile = {
            "user_id": user_id,
            "username": username or "Unknown",
            "created_at": datetime.now().isoformat(),
            "personal_info": {},
            "preferences": {},
            "facts": [],
            "topics_of_interest": [],
            "conversation_history": [],
            "last_interaction": None
        }
        
        # Save the new profile immediately to ensure the file exists
        self.save_user_data(user_id, new_profile)
        logging.info(f"Created new user profile for {user_id}")
        
        return new_profile

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
        
        # Keep only the configured number of conversations
        if len(user_data["conversation_history"]) > Config.MAX_CONVERSATION_HISTORY:
            user_data["conversation_history"] = user_data["conversation_history"][-Config.MAX_CONVERSATION_HISTORY:]
        
        # Save the updated data
        self.save_user_data(user_id, user_data)
        
        # Also extract facts from this conversation
        await self.extract_and_save_facts(user_id, message_content, username)
        
    async def extract_and_save_facts(self, user_id: str, message_content: str, username: str = None) -> bool:
        """Extract and save facts about the user using AI"""
        if not message_content or (message_content.startswith('/') and len(message_content) < 4):
            return False
            
        user_data = self.load_user_data(user_id, username)  # Pass username to load_user_data
        made_changes = False
        
        try:
            # Extract facts using AI with improved prompt
            fact_prompt = f"""
            Extract factual statements about the user from this message.
            Format the response as a JSON array of complete facts, with each fact being a complete statement.
            Only extract DEFINITE facts about the user, not hypotheticals or preferences.
            If there are no facts, return an empty array [].
            
            The user's message is: {message_content}
            """
            
            try:
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
                    
        except Exception as e:
            # Catch-all error handler
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
    
    def _fact_exists(self, user_data: Dict[str, Any], new_fact: str) -> bool:
        """Check if a similar fact already exists for this user"""
        # Return early if no fact to check
        if not new_fact or not user_data["facts"]:
            return not new_fact  # Return True if new_fact is empty, False if facts are empty
            
        # Convert once to lowercase for efficiency
        new_fact_lower = new_fact.lower()
        
        # walrus operator (:=) here lets us assign and check in one go - pretty slick
        # we're checking if facts match exactly OR are subsets of each other
        return any(
            # Check if facts are same or contained within each other
            (content_lower := (fact["content"] if isinstance(fact, dict) and "content" in fact else fact).lower()) == new_fact_lower
            or new_fact_lower in content_lower 
            or content_lower in new_fact_lower
            for fact in user_data["facts"] if fact  # Skip None or empty facts
        )
            
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
        
    async def get_user_summary(self, user_id: str, username: str = None) -> str:
        """Generate a summary of stored user data"""
        # Sanitize Discord user ID
        user_id = re.sub(r'[^0-9]', '', user_id)
        user_data = self.load_user_data(user_id, username)
        
        summary = []
        
        # Add facts with proper formatting
        if user_data["facts"]:
            summary.append(f"What I know about {user_data.get('username', 'you')}:")
            
            # Extract fact contents
            facts = [fact["content"] if isinstance(fact, dict) and "content" in fact else fact 
                    for fact in user_data["facts"]]
            
            try:
                # Convert facts to second person using AI
                conversion_prompt = PERSPECTIVE_CONVERSION_PROMPT.format(facts=json.dumps(facts))
                response = await self.fact_model.generate_content_async(conversion_prompt)
                converted_text = response.text.strip()
                
                # Handle markdown formatting
                if "```json" in converted_text:
                    converted_text = converted_text.split("```json")[1].split("```")[0].strip()
                elif "```" in converted_text:
                    converted_text = converted_text.split("```")[1].strip()
                
                try:
                    converted_facts = json.loads(converted_text)
                    for fact in converted_facts:
                        if fact:  # Skip empty facts
                            # Ensure first letter is capitalized
                            fact = fact[0].upper() + fact[1:] if fact else fact
                            summary.append(f"- {fact}")
                except json.JSONDecodeError:
                    # Fallback: just use original facts if conversion fails
                    for fact in facts:
                        if fact:  # Skip empty facts
                            fact = fact[0].upper() + fact[1:] if fact else fact
                            summary.append(f"- {fact}")
            except Exception as e:
                logging.error(f"Error converting facts: {e}")
                # Fallback: use original facts
                for fact in facts:
                    if fact:  # Skip empty facts
                        fact = fact[0].upper() + fact[1:] if fact else fact
                        summary.append(f"- {fact}")
                
        # Add topics of interest
        if user_data["topics_of_interest"]:
            summary.append("\nYour interests:")
            for topic in user_data["topics_of_interest"]:
                summary.append(f"- {topic}")
                
        # Add interaction stats
        if user_data.get("conversation_history"):
            first_interaction = datetime.fromisoformat(user_data["created_at"])
            total_convos = len(user_data.get("conversation_history", []))
            summary.append(f"\nWe've talked {total_convos} times since {first_interaction.strftime('%B %d, %Y')}")
            
        if not summary:
            return "I don't have any information saved about you yet."
            
        return "\n".join(summary)

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