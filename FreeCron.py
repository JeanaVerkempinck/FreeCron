import discord
from discord.ext import commands
from discord.utils import get
from datetime import datetime, timedelta
import json
import re
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Initialize bot with intents
intents = discord.Intents.default()
intents.typing = False
intents.presences = False
intents.messages = True
intents.message_content = True

# Replace with your bot token
TOKEN = 'YOUR_BOT_TOKEN'
bot = commands.Bot(command_prefix='/', intents=intents)

# In-memory data storage (Replace with a database like SQLite for persistence)
users_config = {}  # Structure: {user_id: {timezone: str, tags: list, off_limit_weekdays: str, off_limit_weekends: str}}
users_crons = {}   # Structure: {user_id: [crons]}

# Helper function to load and save user data from a JSON file
def load_data():
    global users_config, users_crons
    try:
        with open('user_data.json', 'r') as f:
            data = json.load(f)
            users_config = data.get('config', {})
            users_crons = data.get('crons', {})
    except FileNotFoundError:
        save_data()

def save_data():
    with open('user_data.json', 'w') as f:
        json.dump({'config': users_config, 'crons': users_crons}, f)

# Helper function to validate time format
def is_valid_time_format(time_str):
    # Matches formats like 0000-1500, 1630-4+, *-2000, etc.
    pattern = r'^(\d{4}-\d{4}|\d{4}-\d+\+|-\d+-\d{4}|\*-?\d{4}|\d{4}-\*)$'
    return re.match(pattern, time_str) is not None

# Command to set user configuration
@bot.command(name='setconfig')
async def set_user_config(ctx, timezone: str, off_limit_weekdays: str = '', off_limit_weekends: str = ''):
    user_id = str(ctx.author.id)

    # Validate timezone (example validation, you may need a more comprehensive check)
    if timezone.upper() not in ['UTC', 'EST', 'CST', 'MST', 'PST']:
        await ctx.send(f"Invalid timezone. Please provide a valid timezone (e.g., UTC, EST, CST, MST, PST).")
        return

    # Validate off-limit times (allow empty input)
    if off_limit_weekdays and not is_valid_time_format(off_limit_weekdays):
        await ctx.send("Invalid weekday off-limit time format. Please provide times in the format 'HHMM-HHMM'.")
        return

    if off_limit_weekends and not is_valid_time_format(off_limit_weekends):
        await ctx.send("Invalid weekend off-limit time format. Please provide times in the format 'HHMM-HHMM'.")
        return

    # Update the user configuration
    users_config[user_id] = {
        'timezone': timezone,
        'tags': ['cron'],
        'off_limit_weekdays': off_limit_weekdays,
        'off_limit_weekends': off_limit_weekends
    }
    save_data()
    await ctx.send(f"Configuration updated for {ctx.author.display_name}.")

# Command to add a tag
@bot.command(name='addtag')
async def add_tag(ctx, tag: str):
    user_id = str(ctx.author.id)
    if user_id not in users_config:
        users_config[user_id] = {
            'timezone': 'UTC',
            'tags': ['cron'],
            'off_limit_weekdays': '',
            'off_limit_weekends': ''
        }

    # Check if the tag is 'kord' and if the user has the admin role
    if tag.lower() == 'kord':
        if not ctx.author.guild_permissions.administrator:
            await ctx.send("Only users with the admin role can add the 'kord' tag.")
            return
    if tag.lower() not in users_config[user_id]['tags']:
        users_config[user_id]['tags'].append(tag.lower())
        save_data()
        await ctx.send(f"Tag '{tag}' added to {ctx.author.display_name}.")
    else:
        await ctx.send(f"You already have the tag '{tag}'.")

# Command to remove a tag
@bot.command(name='removetag')
async def remove_tag(ctx, tag: str):
    user_id = str(ctx.author.id)

    if user_id not in users_config or tag.lower() not in users_config[user_id]['tags']:
        await ctx.send(f"You don't have the tag '{tag}' to remove.")
        return

    # Check for 'cron' tag removal and warn the user
    if tag.lower() == 'cron':
        await ctx.send("Warning: Removing the 'cron' tag will delete all your FreeCron configurations and entries.")
        users_config.pop(user_id, None)
        users_crons.pop(user_id, None)
        save_data()
        await ctx.send("Your FreeCron data has been purged.")
        return

    # Only allow admins to remove the 'kord' tag
    if tag.lower() == 'kord' and not ctx.author.guild_permissions.administrator:
        await ctx.send("Only users with the admin role can remove the 'kord' tag.")
        return

    users_config[user_id]['tags'].remove(tag.lower())
    save_data()
    await ctx.send(f"Tag '{tag}' removed from {ctx.author.display_name}.")

# Command to add a cron entry
@bot.command(name='addcron')
async def add_cron(ctx, action: str, month: str, day: str, time_slot: str, note: str, tags: str = ''):
    user_id = str(ctx.author.id)

    # Check if user has the 'kord' tag for 'K' action
    if action.upper() == 'K' and 'kord' not in users_config.get(user_id, {}).get('tags', []):
        await ctx.send("You don't have the 'kord' tag required to create an event.")
        return

    # Validate action
    if action.upper() not in ['A', 'N', 'R', 'K']:
        await ctx.send("Invalid action. Use 'A' for available, 'N' for not available, 'R' for repeating, or 'K' for an event.")
        return

    # Validate 'K' action specifics
    if action.upper() == 'K':
        if ',' in month or '-' in month or '*' in month:
            await ctx.send("Event month for 'K' action must be a single specified month (e.g., '04').")
            return
        if ',' in day or '-' in day or '*' in day:
            await ctx.send("Event day for 'K' action must be a single specified day (e.g., '15').")
            return
        if not time_slot.isdigit() or len(time_slot) != 4:
            await ctx.send("Event start time must be in HHMM format (e.g., '0900').")
            return
        if len(note.strip()) == 0 or note == '.':
            await ctx.send("Event must have a title in the note field.")
            return
        if not tags:
            await ctx.send("Event must specify tags or groups in parentheses for invited users (e.g., '(tag1,tag2)').")
            return

        # Check for valid tags format
        if not (tags.startswith('(') and tags.endswith(')')):
            await ctx.send("Tags for 'K' action must be in parentheses (e.g., '(tag1,tag2)').")
            return

    # Process and save the cron or kron
    cron_entry = {
        'action': action.upper(),
        'month': month,
        'day': day,
        'time_slot': time_slot,
        'note': note if note.endswith('.') else note + '.',
        'tags': tags.strip()
    }

    if user_id not in users_crons:
        users_crons[user_id] = []
    users_crons[user_id].append(cron_entry)
    save_data()

    if action.upper() == 'K':
        await process_kron_event(ctx, cron_entry)
    else:
        await ctx.send(f"Cron added for {ctx.author.display_name}: {cron_entry}")

# Function to process the 'K' event (kron)
async def process_kron_event(ctx, kron):
    # Extract the tags from the kron entry
    tags = kron['tags'][1:-1].split(',')

    # Find users with the specified tags
    affected_users = [user_id for user_id, config in users_config.items() if any(tag.strip() in config.get('tags', []) for tag in tags)]

    # Send direct messages to affected users
    for user_id in affected_users:
        user = await bot.fetch_user(user_id)
        if user:
            try:
                await user.send(f"You have been invited to an event: {kron['note']}. Start Time: {kron['time_slot']}")
            except discord.Forbidden:
                await ctx.send(f"Could not send a DM to {user.display_name}.")

    # Announce the event in the server
    mentions = ', '.join([f"<@{user_id}>" for user_id in affected_users])
    await ctx.send(f"New event created by {ctx.author.display_name}: {kron['note']}. Start Time: {kron['time_slot']}.\nInvited: {mentions}")

    # Google Calendar event creation
    await create_google_event(ctx, ctx.author, kron, affected_users)

async def create_google_event(ctx, organizer, kron, attendees):
    # Initialize Google Calendar API
    SCOPES = ['https://www.googleapis.com/auth/calendar']
    SERVICE_ACCOUNT_FILE = 'path/to/credentials.json'  # Replace with your service account credentials
    credentials = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)

    service = build('calendar', 'v3', credentials=credentials)

    # Prepare event details
    event = {
        'summary': kron['note'],
        'start': {
            'dateTime': f"{kron['month']}-{kron['day']}T{kron['time_slot'][:2]}:{kron['time_slot'][2:]}:00",
            'timeZone': users_config[str(ctx.author.id)]['timezone'],
        },
        'end': {
            'dateTime': f"{kron['month']}-{kron['day']}T{kron['time_slot'][:2]}:{str(int(kron['time_slot'][2:]) + 60)}:00",  # Example: 1 hour duration
            'timeZone': users_config[str(ctx.author.id)]['timezone'],
        },
        'attendees': [{'email': get_user_email(user_id)} for user_id in attendees if get_user_email(user_id)],
        'organizer': {'email': get_user_email(ctx.author.id)}  # Event organizer
    }

    # Insert the event in the calendar
    event = service.events().insert(calendarId='primary', body=event).execute()

    await ctx.send(f"Google Calendar event created: {event.get('htmlLink')}")

def get_user_email(user_id):
    # Lookup user's email address from your database or configuration
    return "user_email@example.com"  # Replace with actual implementation

# Help command to provide examples and explanations
@bot.command(name='help')
async def help_command(ctx):
    help_message = """
**FreeCron Bot Help**

1. **/setconfig [timezone] [off_limit_weekdays] [off_limit_weekends]**
   - Set your timezone and off-limit times. Example: `/setconfig UTC 0000-1500 2000-0600`
   
2. **/addtag [tag]**
   - Add a custom tag to yourself. Example: `/addtag gamer`

3. **/removetag [tag]**
   - Remove a tag from yourself. Example: `/removetag gamer`
   - Warning: Removing the 'cron' tag will delete all your FreeCron configurations and entries.

4. **/addcron [action] [month] [day] [time_slot] [note] [tags (optional)]**
   - Add a cron or event.
   - Actions: 'A' (Available), 'N' (Not Available), 'R' (Repeating), 'K' (Event).
   - Example: `/addcron K 04 15 0900 Meeting (team)`

5. **Examples of Time Formats:**
   - '0000-1500': Represents from midnight to 3 PM.
   - '1630-4+': Start at 4:30 PM and available for about 4 hours.
   - '1300-*': Available from 1 PM to end of the day.
   """
    await ctx.send(help_message)

# Purge users without 'cron' tag
async def purge_users():
    for user_id in list(users_config.keys()):
        if 'cron' not in users_config[user_id]['tags']:
            users_config.pop(user_id, None)
            users_crons.pop(user_id, None)
    save_data()

# Event handler for when the bot is ready
@bot.event
async def on_ready():
    print(f'FreeCron is ready and logged in as {bot.user}!')
    load_data()
    await purge_users()

# Run the bot
bot.run(TOKEN)
