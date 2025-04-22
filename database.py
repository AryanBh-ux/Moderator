import json
import os
from threading import Lock
from functools import lru_cache
from typing import Dict, List, Optional, Union
from typing import TypedDict
import matplotlib.pyplot as plt
import io
import base64
from datetime import datetime
import supabase
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Initialize Supabase client
supabase_url = os.getenv('SUPABASE_URL')
supabase_key = os.getenv('SUPABASE_KEY')
supabase_client = supabase.create_client(supabase_url, supabase_key)

# Global lock for thread-safe database operations
db_lock = Lock()

class AnalyticsResult(TypedDict):
    total_blocks: int
    daily_blocks: dict[str, int]
    user_block_pie: str  # Base64 encoded PNG

def setup_database():
    """Initialize the database with required tables"""
    # This is handled via Supabase migrations or UI
    # Tables should be created in Supabase dashboard before running the bot
    pass

# In database.py, modify the load_roles_data function:
@lru_cache(maxsize=128)
def load_roles_data(guild_id: Optional[Union[int, str]] = None) -> Union[Dict, Optional[Dict]]:
    """Load roles data for a specific guild or all guilds"""
    with db_lock:
        try:
            if guild_id:
                response = supabase_client.table('roles_data').select('*').eq('guild_id', str(guild_id)).execute()
                if response.data and len(response.data) > 0:
                    row = response.data[0]
                    return {
                        "owner_id": int(row['owner_id']) if row['owner_id'] and row['owner_id'] != 'None' else None,
                        "allowed_roles": json.loads(row['allowed_roles']) if row['allowed_roles'] else [],
                        "immune_roles": json.loads(row['immune_roles']) if row['immune_roles'] else []
                    }
                return None
            
            response = supabase_client.table('roles_data').select('*').execute()
            return {row['guild_id']: {
                "owner_id": int(row['owner_id']) if row['owner_id'] and row['owner_id'] != 'None' else None,
                "allowed_roles": json.loads(row['allowed_roles']) if row['allowed_roles'] else [],
                "immune_roles": json.loads(row['immune_roles']) if row['immune_roles'] else []
            } for row in response.data}
        except json.JSONDecodeError as e:
            print(f"[ERROR] JSON decode error in load_roles_data: {e}")
            return None if guild_id else {}
        except Exception as e:
            print(f"[ERROR] Failed to load roles data: {e}")
            return None if guild_id else {}

# Also update the save_roles_data function:
def save_roles_data(guild_id: Union[int, str], data: Dict) -> bool:
    """Save roles data for a guild"""
    with db_lock:
        try:
            # Check if record exists
            response = supabase_client.table('roles_data').select('*').eq('guild_id', str(guild_id)).execute()
            
            row_data = {
                'guild_id': str(guild_id),
                'owner_id': str(data.get("owner_id", "")) if data.get("owner_id") else None,
                'allowed_roles': json.dumps(data.get("allowed_roles", [])),
                'immune_roles': json.dumps(data.get("immune_roles", []))
            }
            
            if response.data and len(response.data) > 0:
                # Update existing record
                supabase_client.table('roles_data').update(row_data).eq('guild_id', str(guild_id)).execute()
            else:
                # Insert new record
                supabase_client.table('roles_data').insert(row_data).execute()
                
            load_roles_data.cache_clear()
            return True
        except Exception as e:
            print(f"[ERROR] Failed to save roles data: {e}")
            return False
        
def save_roles_data(guild_id: Union[int, str], data: Dict) -> bool:
    """Save roles data for a guild"""
    with db_lock:
        try:
            # Check if record exists
            response = supabase_client.table('roles_data').select('*').eq('guild_id', str(guild_id)).execute()
            
            row_data = {
                'guild_id': str(guild_id),
                'owner_id': str(data.get("owner_id", "")),
                'allowed_roles': json.dumps(data.get("allowed_roles", [])),
                'immune_roles': json.dumps(data.get("immune_roles", []))
            }
            
            if response.data and len(response.data) > 0:
                # Update existing record
                supabase_client.table('roles_data').update(row_data).eq('guild_id', str(guild_id)).execute()
            else:
                # Insert new record
                supabase_client.table('roles_data').insert(row_data).execute()
                
            load_roles_data.cache_clear()
            return True
        except Exception as e:
            print(f"[ERROR] Failed to save roles data: {e}")
            return False

def get_roles_data(guild: Union[object, int, str]) -> Dict:
    """Get roles data for a guild, creating default if not exists"""
    try:
        guild_id = guild.id if hasattr(guild, 'id') else guild
        data = load_roles_data(guild_id)
        
        if not data:
            owner_id = guild.owner_id if hasattr(guild, 'owner_id') else None
            data = {
                "owner_id": owner_id,
                "allowed_roles": [],
                "immune_roles": []
            }
            if not save_roles_data(guild_id, data):
                print(f"[WARNING] Failed to save default roles data for guild {guild_id}")
        
        return data
    except Exception as e:
        print(f"[ERROR] get_roles_data failed: {e}")
        return {"owner_id": None, "allowed_roles": [], "immune_roles": []}

@lru_cache(maxsize=128)
def load_swear_data(guild_id: Union[int, str]) -> Optional[Dict]:
    """Load swear data for a specific guild"""
    with db_lock:
        try:
            response = supabase_client.table('swear_data').select('*').eq('guild_id', str(guild_id)).execute()
            if response.data and len(response.data) > 0:
                row = response.data[0]
                return {
                    "swear_words": json.loads(row['swear_words']) if row['swear_words'] else [],
                    "allowed_channels": json.loads(row['allowed_channels']) if row['allowed_channels'] else []
                }
            return None
        except json.JSONDecodeError as e:
            print(f"[ERROR] JSON decode error in load_swear_data: {e}")
            return None
        except Exception as e:
            print(f"[ERROR] Failed to load swear data: {e}")
            return None

def save_swear_data(guild_id: Union[int, str], data: Dict) -> bool:
    """Save swear data for a guild"""
    with db_lock:
        try:
            # Check if record exists
            response = supabase_client.table('swear_data').select('*').eq('guild_id', str(guild_id)).execute()
            
            row_data = {
                'guild_id': str(guild_id),
                'swear_words': json.dumps(data.get("swear_words", [])),
                'allowed_channels': json.dumps(data.get("allowed_channels", []))
            }
            
            if response.data and len(response.data) > 0:
                # Update existing record
                supabase_client.table('swear_data').update(row_data).eq('guild_id', str(guild_id)).execute()
            else:
                # Insert new record
                supabase_client.table('swear_data').insert(row_data).execute()
                
            load_swear_data.cache_clear()
            return True
        except Exception as e:
            print(f"[ERROR] Failed to save swear data: {e}")
            return False

def get_swear_data(guild_id: Union[int, str]) -> Dict:
    """Get swear data for a guild, creating default if not exists"""
    try:
        data = load_swear_data(guild_id)
        
        if not data:
            data = {
                "swear_words": [],
                "allowed_channels": []
            }
            if not save_swear_data(guild_id, data):
                print(f"[WARNING] Failed to save default swear data for guild {guild_id}")
        
        return data
    except Exception as e:
        print(f"[ERROR] get_swear_data failed: {e}")
        return {"swear_words": [], "allowed_channels": []}

@lru_cache(maxsize=128)
def load_guild_settings(guild_id: Union[int, str]) -> Dict:
    """Load guild-specific settings"""
    with db_lock:
        try:
            response = supabase_client.table('guild_settings').select('*').eq('guild_id', str(guild_id)).execute()
            default_settings = {
                'strict_mode': False,
                'warning_message': None,
                'cooldown_time': 60,
                'max_warnings': 3
            }
            
            if response.data and len(response.data) > 0:
                row = response.data[0]
                return {
                    'strict_mode': bool(row['strict_mode']),
                    'warning_message': row['warning_message'],
                    'cooldown_time': row['cooldown_time'],
                    'max_warnings': row['max_warnings']
                }
            return default_settings
        except Exception as e:
            print(f"[ERROR] Failed to load guild settings: {e}")
            return default_settings

def save_guild_settings(guild_id: Union[int, str], settings: Dict) -> bool:
    """Save guild-specific settings"""
    with db_lock:
        try:
            # Check if record exists
            response = supabase_client.table('guild_settings').select('*').eq('guild_id', str(guild_id)).execute()
            
            row_data = {
                'guild_id': str(guild_id),
                'strict_mode': int(settings.get('strict_mode', False)),
                'warning_message': settings.get('warning_message'),
                'cooldown_time': int(settings.get('cooldown_time', 60)),
                'max_warnings': int(settings.get('max_warnings', 3))
            }
            
            if response.data and len(response.data) > 0:
                # Update existing record
                supabase_client.table('guild_settings').update(row_data).eq('guild_id', str(guild_id)).execute()
            else:
                # Insert new record
                supabase_client.table('guild_settings').insert(row_data).execute()
                
            load_guild_settings.cache_clear()
            return True
        except Exception as e:
            print(f"[ERROR] Failed to save guild settings: {e}")
            return False

# Add to database.py
def load_logging_channel(guild_id: Union[int, str]) -> Optional[int]:
    """Load the logging channel ID for a guild"""
    with db_lock:
        try:
            response = supabase_client.table('guild_settings').select('logging_channel').eq('guild_id', str(guild_id)).execute()
            if response.data and len(response.data) > 0:
                return int(response.data[0]['logging_channel']) if response.data[0]['logging_channel'] else None
            return None
        except Exception as e:
            print(f"[ERROR] Failed to load logging channel: {e}")
            return None

def save_logging_channel(guild_id: Union[int, str], channel_id: Optional[int]) -> bool:
    """Save the logging channel ID for a guild"""
    with db_lock:
        try:
            # Check if record exists
            response = supabase_client.table('guild_settings').select('*').eq('guild_id', str(guild_id)).execute()
            
            row_data = {
                'guild_id': str(guild_id),
                'logging_channel': str(channel_id) if channel_id else None
            }
            
            if response.data and len(response.data) > 0:
                # Update existing record
                supabase_client.table('guild_settings').update(row_data).eq('guild_id', str(guild_id)).execute()
            else:
                # Insert new record
                supabase_client.table('guild_settings').insert(row_data).execute()
                
            load_guild_settings.cache_clear()
            return True
        except Exception as e:
            print(f"[ERROR] Failed to save logging channel: {e}")
            return False
# Update in database.py

def log_violation(
    guild_id: int,
    user_id: int,
    username: str,
    channel_id: int,
    message: str,
    timestamp: str,
    discriminator: Optional[str] = None
) -> bool:
    """Log a moderation violation to the database"""
    with db_lock:
        try:
            # Prepare the data to insert
            log_data = {
                'guild_id': str(guild_id),
                'user_id': str(user_id),
                'username': username,
                'channel_id': str(channel_id),
                'message': message,
                'timestamp': timestamp
            }
            
            # Only include discriminator if provided
            if discriminator:
                log_data['discriminator'] = discriminator
                
            # Insert into Supabase
            response = supabase_client.table('moderation_logs').insert(log_data).execute()
            
            # Check if the insert was successful
            if hasattr(response, 'data') and response.data:
                return True
            return False
                
        except Exception as e:
            print(f"[DB ERROR] Failed to log violation: {e}")
            return False

def get_analytics(guild_id: int) -> AnalyticsResult:
    """Generate graphical analytics data"""
    with db_lock:
        try:
            # Get raw data
            logs = supabase_client.table('moderation_logs') \
                .select('*') \
                .eq('guild_id', str(guild_id)) \
                .execute().data

            # 1. Total blocks count
            total = len(logs)

            # 2. Daily blocks
            daily = {}
            for log in logs:
                date = log['timestamp'][:10]  # YYYY-MM-DD
                daily[date] = daily.get(date, 0) + 1

            # 3. User-wise pie chart
            user_counts = {}
            for log in logs:
                user = f"{log['username']}#{log['discriminator']}"
                user_counts[user] = user_counts.get(user, 0) + 1


            
            # Generate pie chart
            plt.figure(figsize=(8, 6))
            plt.pie(
                user_counts.values(),
                labels=user_counts.keys(),
                autopct='%1.1f%%',
                startangle=140
            )
            plt.title('Blocks by User')
            buf = io.BytesIO()
            plt.savefig(buf, format='png')
            plt.close()
            buf.seek(0)
            pie_b64 = base64.b64encode(buf.read()).decode('utf-8')

            return {
                'total_blocks': total,
                'daily_blocks': daily,
                'user_block_pie': pie_b64
            }

        except Exception as e:
            print(f"[ERROR] Analytics generation failed: {e}")
            return {
                'total_blocks': 0,
                'daily_blocks': {},
                'user_block_pie': ""
            }
def get_violation_logs(guild_id: int, limit: int = 50) -> List[Dict]:
    """Retrieve moderation logs"""
    with db_lock:
        try:
            response = supabase_client.table('moderation_logs').select('*').eq('guild_id', str(guild_id)).order('timestamp', desc=True).limit(limit).execute()
            return response.data
        except Exception as e:
            print(f"[ERROR] Failed to get logs: {e}")
            return []

