#!/usr/bin/env python3
"""Fix 2FA code generation from alert to separate message"""

import re

# Read the bot.py file
with open('bot.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace the problematic lines
# First alert_message line
content = re.sub(
    r'alert_message = f"📲 کد 2FA شما: \{code\}\\n\\n⏰ اعتبار \{remaining_seconds\} ثانیه"',
    r'message_text = f"📲 *کد 2FA شما:*\\n\\n`{code}`\\n\\n⏰ این کد به مدت {remaining_seconds} ثانیه معتبر است."',
    content
)

# Second alert_message line  
content = re.sub(
    r'alert_message = f"📲 کد 2FA شما: \{code\}\\n\\n⏰ اعتبار \{remaining_seconds\} ثانیه \(دفعهٔ دوم\)"',
    r'message_text = f"📲 *کد 2FA شما:*\\n\\n`{code}`\\n\\n⏰ این کد به مدت {remaining_seconds} ثانیه معتبر است (دفعهٔ دوم)."',
    content
)

# Replace the show_alert=True line
content = re.sub(
    r'# Show alert with code and TTL\s*\n\s*await query\.answer\(alert_message, show_alert=True\)',
    '''# Answer callback query first
                    await query.answer()
                    
                    # Send 2FA code as a separate message
                    await context.bot.send_message(
                        chat_id=user.id,
                        text=message_text,
                        parse_mode="Markdown"
                    )''',
    content,
    flags=re.MULTILINE
)

# Write the modified content back
with open('bot.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("✅ Fixed 2FA code generation in bot.py")
print("🔄 Please restart the bot container now") 