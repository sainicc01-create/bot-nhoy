# -*- coding: utf-8 -*-
import logging
import asyncio
from datetime import datetime
import aiohttp
import json
import os # Import os module is crucial for environment variables
from typing import Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.helpers import escape_markdown
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.error import InvalidToken


# --- CONFIGURATION (MUST BE SET AS ENVIRONMENT VARIABLES ON RENDER) ---
# FIX 1: Use os.getenv() for robust cloud deployment and configuration
# We set the default values to the actual tokens/IDs for easy local testing, 
# but in a production environment like Render, the values must come from the OS environment.
BOT_TOKEN = os.getenv("BOT_TOKEN", "7586151294:AAE56w1KsB01qmfebOY4jccne2VI11ueMqM")
BOT_2_TOKEN = os.getenv("BOT_2_TOKEN", "6994395596:AAGaw7m9reS-wcJvozclC4D6JniZK9LrdqM")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "1732455712")
BOT_2_ADMIN_CHAT_ID = os.getenv("BOT_2_ADMIN_CHAT_ID", "1732455712")

# FIX 2: FASTAPI_API_URL must be an environment variable on Render
FASTAPI_API_URL = os.getenv("FASTAPI_API_URL", "http://127.0.0.1:8000")
FASTAPI_ADMIN_TOKEN = os.getenv("FASTAPI_ADMIN_TOKEN", "admin_token") # Must match token in main.py


# --- ASSET URLs (Remain unchanged) ---
START_PHOTO_URL = "https://i.pinimg.com/736x/dd/cb/03/ddcb0341971d4836da7d12c399149675.jpg"
PAYMENT_PHOTO_URL = "https://i.pinimg.com/736x/01/ab/75/01ab75af562098fc4774fdbd222b2132.jpg"
QR_PHOTO_URL = "https://i.pinimg.com/736x/14/70/c4/1470c436182cf4c4142bfa343b45c844.jpg"
SUCCESS_PHOTO_URL = "https://i.pinimg.com/736x/da/1f/3b/da1f3b1746d1d05cfa59f371d0310f8a.jpg"
REJECTED_PHOTO_URL = "https://i.pinimg.com/originals/a5/75/0b/a5750babcf0f417f30e0b4773b29e376.gif"
ALERT_PHOTO_URL = "https://i.pinimg.com/736x/eb/41/ca/eb41ca25e4d9bfc312fb81e59440f0ce.jpg"

# --- LOGGING SETUP ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- IN-MEMORY USER DATA STORAGE ---
user_data = {}
pending_approvals = {}
completed_orders = {}

# --- HELPER FUNCTIONS ---

async def create_fastapi_order(user_id: int, username: str, udid: str, payment_option: str, photo_file: object) -> Optional[int]:
    """Uploads file to FastAPI and creates a new order entry."""
    url = f"{FASTAPI_API_URL}/orders"
    username_clean = username.replace('@', '') if username.startswith('@') else username
    name_with_price = f"{username_clean} (${payment_option} Plan)"

    try:
        file_bytes = await photo_file.download_as_bytearray()
    except Exception as e:
        logger.error(f"Failed to download photo file: {e}")
        return None
    
    data = aiohttp.FormData()
    data.add_field('name', name_with_price)
    data.add_field('udid', udid)
    data.add_field('image', file_bytes, filename=f'{user_id}_payment.jpg', content_type='image/jpeg')
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=data, timeout=30) as response:
                if response.status == 200 or response.status == 201:
                    result = await response.json()
                    logger.info(f"Successfully created FastAPI order {result.get('id')} for user {user_id}")
                    return result.get('id')
                else:
                    response_text = await response.text()
                    logger.error(f"Failed to create FastAPI order. Status: {response.status}, Response: {response_text}")
                    return None
    except Exception as e:
        logger.error(f"Exception while creating FastAPI order: {e}")
        return None

async def update_fastapi_order_status(order_id: int, status: str) -> bool:
    """Updates the status of an order in the FastAPI database."""
    url = f"{FASTAPI_API_URL}/orders/{order_id}/status"
    headers = {"Authorization": f"Bearer {FASTAPI_ADMIN_TOKEN}"}
    payload = {"status": status}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.put(url, headers=headers, json=payload, timeout=10) as response:
                if response.status == 200:
                    logger.info(f"Successfully updated FastAPI order {order_id} to {status}")
                    return True
                else:
                    response_text = await response.text()
                    logger.error(f"Failed to update FastAPI order status. Status: {response.status}, Response: {response_text}")
                    return False
    except Exception as e:
        logger.error(f"Exception while updating FastAPI order status: {e}")
        return False

async def send_alert_after_30s(user_id: int) -> None:
    """Sends alert photo after 30 seconds delay."""
    try:
        logger.info(f"Scheduling 30-day alert for user {user_id}")
        await asyncio.sleep(6)  # Use 6s for testing, change to 2592000 for 30 days
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        payload = {
            'chat_id': str(user_id),
            'photo': ALERT_PHOTO_URL,
            'caption': " Oder esign muy tt b dach jit mes b 🥺 \n\n " 
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=payload, timeout=aiohttp.ClientTimeout(total=30)) as response:
                if response.status == 200:
                    logger.info(f"Successfully sent 30-day alert to user {user_id}")
                else:
                    response_text = await response.text()
                    logger.error(f"Failed to send 30-day alert. Status: {response.status}, Response: {response_text}")
    except Exception as e:
        logger.error(f"Error sending 30-day alert to user {user_id}: {e}")

async def send_to_bot_2_for_approval(user_id: int, username: str, udid: str, payment_option: str, order_id: int) -> bool:
    """Sends approval request to Bot 2 admin using direct HTTP request."""
    url = f"https://api.telegram.org/bot{BOT_2_TOKEN}/sendMessage"
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    message_text = (
        f"🔍 NEW APPROVAL REQUEST\n\n"
        f"👤 User: {username}\n"
        f"🆔 User ID: {user_id}\n"
        f"📦 Order ID: {order_id}\n" 
        f"📱 UDID: {udid}\n"
        f"💳 Payment Option: {payment_option}\n"
        f"⏰ Time: {current_time}\n\n"
        f"Please review and decide:"
    )
    
    keyboard = [
        [
            {"text": "✅ Approve", "callback_data": f"approve_{order_id}"},
            {"text": "❌ Reject", "callback_data": f"reject_{order_id}"}
        ],
        [
            {"text": "📋 Copy UDID", "callback_data": f"copyudid_{user_id}_{order_id}"}
        ]
    ]
    
    payload = {
        'chat_id': BOT_2_ADMIN_CHAT_ID,
        'text': message_text,
        'reply_markup': json.dumps({"inline_keyboard": keyboard})
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=payload, timeout=aiohttp.ClientTimeout(total=30)) as response:
                response_text = await response.text()
                logger.info(f"Bot 2 response status: {response.status}")
                logger.info(f"Bot 2 response: {response_text}")
                
                if response.status == 200:
                    logger.info(f"Successfully sent approval request to Bot 2 for user {user_id}")
                    return True
                else:
                    logger.error(f"Failed to send to Bot 2. Status: {response.status}, Response: {response_text}")
                    return False
    except Exception as e:
        logger.error(f"Exception while sending to Bot 2: {e}")
        return False

async def send_response_to_user(user_id: int, approved: bool, order_id: int) -> bool:
    """Sends approval/rejection response to user via Bot 1 and updates FastAPI status."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    
    user_info = pending_approvals.get(user_id, {})
    
    if approved:
        if not await update_fastapi_order_status(order_id, 'approved'):
             logger.error(f"Failed to update FastAPI status to approved for order {order_id}.")
        
        photo_url = SUCCESS_PHOTO_URL
        username = user_info.get('username', 'User')
        udid = user_info.get('udid', 'N/A')
        payment_option = user_info.get('payment_option', '0')
        
        display_name = username.replace('@', '') if username.startswith('@') else username
        
        completed_orders[user_id] = {
            'username': username,
            'udid': udid,
            'payment_option': payment_option,
            'completion_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'fastapi_order_id': order_id 
        }
        
        caption = (
            f"🎉 *Thank You, {escape_markdown(display_name, version=2)}\\!* 🎉\n\n"
            f"Order has been completed\\.\n\n"
            f"UDID: `{escape_markdown(udid, version=2)}`\n"
            f"Price: `${payment_option}`\n"
            f"Added on: `Cambodia`\n\n"
            f"To start a new order, use /start \n"
            f"To check your completed order, click /Details"
        )
        
        asyncio.create_task(send_alert_after_30s(user_id))
        
    else:
        if not await update_fastapi_order_status(order_id, 'rejected'):
            logger.error(f"Failed to update FastAPI status to rejected for order {order_id}.")
            
        photo_url = REJECTED_PHOTO_URL
        caption = (
            "❌ *Request Not Approved*\n\n"
            "Your request has been reviewed and not approved\\.\n"
            "Please try again or contact support\\."
        )
    
    payload = {
        'chat_id': str(user_id),
        'photo': photo_url,
        'caption': caption,
        'parse_mode': 'MarkdownV2'
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=payload, timeout=aiohttp.ClientTimeout(total=30)) as response:
                if response.status == 200:
                    logger.info(f"Successfully sent response to user {user_id}")
                    return True
                else:
                    response_text = await response.text()
                    logger.error(f"Failed to send response to user. Status: {response.status}, Response: {response_text}")
                    return False
    except Exception as e:
        logger.error(f"Exception while sending response to user: {e}")
        return False

def validate_udid(udid: str) -> bool:
    """Validates UDID format - accepts 20-50 alphanumeric characters and hyphens."""
    if not udid:
        return False
    
    if not 20 <= len(udid) <= 50:
        return False
    
    valid_chars = set('0123456789abcdefABCDEF-')
    if not all(c in valid_chars for c in udid):
        return False
    
    return True

# --- BOT HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message: return
    user = update.effective_user
    user_id = user.id
    if user_id in user_data: del user_data[user_id]
    if user_id in pending_approvals: del pending_approvals[user_id]
    keyboard = [[InlineKeyboardButton("📱 Download UDID Profile", url="https://udid.tech/download-profile")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    caption = (
        f"🎉 *Welcome, {escape_markdown(user.first_name, version=2)}\\!* 🎉\n\n"
        "📋 *How to get started:*\n\n"
        "1️⃣ Click the button below to download the UDID profile\\.\n"
        "2️⃣ Install it on your device\\.\n"
        "3️⃣ Copy your UDID and send it to me\\.\n"
        "4️⃣ Select a payment plan and send the payment proof\\.\n\n"
        "💡 *Need help?* Just follow the steps above\\!"
    )
    try:
        await update.message.reply_photo(
            photo=START_PHOTO_URL, caption=caption, reply_markup=reply_markup, parse_mode='MarkdownV2'
        )
    except Exception as e:
        logger.error(f"Error sending start message: {e}")
        await update.message.reply_text("Welcome! Please use /start to begin.")
    logger.info(f"User {user_id} ({user.username or user.first_name}) started the bot")

async def details_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message: return
    user = update.effective_user
    user_id = user.id
    if user_id not in completed_orders:
        await update.message.reply_text(
            "❌ *No Order Details Found*\n\n"
            "You don't have any completed orders yet\\.\n"
            "Please complete an order first using /start", parse_mode='MarkdownV2'
        )
        return
    order_info = completed_orders[user_id]
    username = order_info['username']
    udid = order_info['udid']
    payment_option = order_info['payment_option']
    completion_time = order_info['completion_time']
    details_text = (
        f"📋 *Order Details*\n\n"
        f"👤 User: `{escape_markdown(username, version=2)}`\n"
        f"🆔 User ID: `{user_id}`\n"
        f"📱 UDID: `{escape_markdown(udid, version=2)}`\n"
        f"💳 Payment: `${payment_option}`\n"
        f"⏰ Completed: `{escape_markdown(completion_time, version=2)}`\n\n"
        f"📍 *Location: Cambodia*\n\n"
        f"To place a new order, use /start"
    )
    try:
        await update.message.reply_text(details_text, parse_mode='MarkdownV2')
    except Exception as e:
        logger.error(f"Error sending order details: {e}")
        await update.message.reply_text("Error retrieving order details.")
    logger.info(f"User {user_id} viewed their order details")

async def handle_udid_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message or not update.message.text: return
    user_id = update.effective_user.id
    udid = update.message.text.strip()
    if not validate_udid(udid):
        await update.message.reply_text(
            "❌ *Invalid UDID Format*\n\n"
            "Please make sure you copied the entire UDID string\\.\n"
            "A valid UDID is 20-50 characters long and contains letters, numbers, and hyphens\\.", parse_mode='MarkdownV2'
        )
        return
    user_data[user_id] = {'udid': udid}
    keyboard = [
        [InlineKeyboardButton("🔴   Esign luck- 4$", callback_data="payment_4")],
        [InlineKeyboardButton("🟡   Esign Basic- 7$", callback_data="payment_7")],
        [InlineKeyboardButton("🟠   Esign Standard- 12$", callback_data="payment_12")],
        [InlineKeyboardButton("🟢  Esign Premium - 16$", callback_data="payment_16")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    caption = (
        f"✅ <b>UDID Received!</b>\n\n"
        f"📱 <b>Your UDID:</b> <code>{udid}</code>\n\n"
        f"💰 <b>Choose Your Plan:</b>\n\n"
        f"🔴 <b> Luck ($4)</b> - Test your luck\n"
        f"   • Duration: Variable (0-12 months)\n"
        f"   • No guarantee\n\n"
        f"🟡 <b> Basic ($7)</b> - Reliable option\n"
        f"   • Duration: Up to 1 year\n"
        f"   • 100 days guarantee\n\n"
        f"🟠 <b>Standard ($12)</b> - Best value\n"
        f"   • Duration: 1 year\n"
        f"   • 300 days guarantee\n\n"
        f"🟢<b> Premium ($16)</b> - Instant & secure\n"
        f"   • Duration: 1 year guaranteed\n"
        f"   • 320 days guarantee\n\n"
        f"⚠️ <b>Note:</b> The $4 option is a luck-based plan with no refunds.\n\n"
        f"👇 <b>Select your preferred plan:</b>"
    )
    try:
        await update.message.reply_photo( 
            photo=PAYMENT_PHOTO_URL, caption=caption, reply_markup=reply_markup, parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Error sending payment options: {e}")
        await update.message.reply_text("Error displaying payment options. Please try again.")
    logger.info(f"User {user_id} submitted UDID: {udid[:10]}...")

async def handle_payment_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.callback_query: return
    query = update.callback_query
    await query.answer()
    if not query.data:
        await query.answer(text="Error: Invalid callback data.", show_alert=True)
        return
    if not isinstance(query.message, Message):
        await query.answer(text="Error: Message not accessible.", show_alert=True)
        return
    user_id = query.from_user.id
    if user_id not in user_data:
        await query.edit_message_text("❌ Session expired. Please use /start again.")
        return
    payment_option = query.data.split('_')[1]
    user_data[user_id]['payment_option'] = payment_option
    plan_descriptions = {
        "4": "🔴 Esign Luck - $4",
        "7": "🟡 Esign Basic - $7", 
        "12": "🟠 Esign Standard - $12",
        "16": "🟢 Esign Premium - $16"
    }
    plan_name = plan_descriptions.get(payment_option, f"Plan ${payment_option}")
    caption = (
        f"💳 *{escape_markdown(plan_name, version=2)}*\n\n"
        f"📱 *UDID:* `{escape_markdown(user_data[user_id]['udid'], version=2)}`\n\n"
        f"📋 *Payment Instructions:*\n"
        f"1️⃣ Scan the QR code below\\.\n"
        f"2️⃣ Make your payment of `${payment_option}`\\.\n"
        f"3️⃣ Take a screenshot of the payment confirmation\\.\n"
        f"4️⃣ Send the screenshot to this chat\\.\n\n"
        f"⏰ *Please complete payment within 30 minutes\\.*"
    )
    try:
        await query.edit_message_caption(
            caption=f"✅ {plan_name} selected. Please follow the payment instructions below.",
            reply_markup=None
        )
        await query.message.reply_photo(
            photo=QR_PHOTO_URL, caption=caption, parse_mode='MarkdownV2'
        )
    except Exception as e:
        logger.error(f"Error handling payment button: {e}")
        await query.answer(text="Error processing payment option. Please try again.", show_alert=True)
    logger.info(f"User {user_id} selected payment option {payment_option}$")

async def handle_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles screenshot uploads, creates order in FastAPI, and sends to Bot 2 for approval."""
    if not update.effective_user or not update.message or not update.message.photo: return
    user = update.effective_user
    user_id = user.id
    
    if user_id not in user_data or 'payment_option' not in user_data[user_id]:
        await update.message.reply_text("❌ Please start the process with /start and select a payment option first.")
        return
    
    if user_id in pending_approvals:
        await update.message.reply_text(
            "⏳ Your request is already being processed. Please wait for admin approval."
        )
        return
    
    try:
        photo_file = await update.message.photo[-1].get_file() 
    except Exception as e:
        logger.error(f"Failed to get file object: {e}")
        await update.message.reply_text("❌ Error getting the photo file object. Please try sending a different photo.")
        return

    username = f"@{user.username}" if user.username else user.first_name
    udid = user_data[user_id]['udid']
    payment_option = user_data[user_id]['payment_option']
    
    # STEP 1: CREATE ORDER IN FASTAPI DATABASE
    order_id = await create_fastapi_order(user_id, username, udid, payment_option, photo_file)
    
    if not order_id:
        await update.message.reply_text(
            "❌ Error: Failed to submit order to the database. Please try again or contact support."
        )
        return
        
    # STEP 2: STORE PENDING APPROVAL
    pending_approvals[user_id] = {
        'username': username,
        'udid': udid,
        'payment_option': payment_option,
        'timestamp': datetime.now(),
        'fastapi_order_id': order_id 
    }
    
    await update.message.reply_text(
        "🔄 *Processing your payment screenshot\\.\\.\\.*\n\n"
        "📋 Your request has been submitted to our admin team\\.\n"
        "⏰ Please wait for approval \\(usually within 1\\-2 hours\\)\\.\n\n"
        "✅ You will receive a notification once processed\\!",
        parse_mode='MarkdownV2'
    )
    
    # STEP 3: SEND APPROVAL REQUEST TO BOT 2
    success = await send_to_bot_2_for_approval(user_id, username, udid, payment_option, order_id)
    
    if not success:
        logger.error(f"Failed to send approval request for user {user_id}")
        await update.message.reply_text(
            "❌ Error sending approval request. Please try again or contact support."
        )
        if user_id in pending_approvals:
            del pending_approvals[user_id]

async def handle_copy_udid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.callback_query: return
    query = update.callback_query
    if not query.data:
        await query.answer(text="❌ Invalid callback data", show_alert=True)
        return
    try:
        _, user_id_str, _ = query.data.split('_', 2)
        user_id = int(user_id_str)
    except (ValueError, IndexError):
        await query.answer(text="❌ Invalid callback data", show_alert=True)
        return
    user_info = pending_approvals.get(user_id)
    if not user_info:
        await query.answer(text="❌ UDID not found or request expired", show_alert=True)
        return
    udid = user_info['udid']
    username = user_info['username']
    if query.message:
        await query.message.reply_text(
            f"📋 *UDID for User {escape_markdown(username, version=2)}:*\n\n"
            f"```\n{udid}\n```\n\n*Click the UDID above to copy it\\.*",
            parse_mode='MarkdownV2'
        )
    else:
        logger.error("query.message is None, cannot send reply_text for copy UDID.")
    logger.info(f"Admin copied UDID for user {user_id}")

async def handle_bot2_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.callback_query: return
    query = update.callback_query
    await query.answer()
    if not query.data:
        await query.answer(text="❌ Invalid callback data", show_alert=True)
        return
    try:
        action, order_id_str = query.data.split('_', 1)
        order_id = int(order_id_str)
    except (ValueError, IndexError):
        await query.answer(text="❌ Invalid callback data", show_alert=True)
        return
    user_id = next((uid for uid, info in pending_approvals.items() if info.get('fastapi_order_id') == order_id), None)
    
    if user_id is None or user_id not in pending_approvals:
        await query.edit_message_text("❌ This request is no longer valid or has already been processed.")
        return
    
    user_info = pending_approvals[user_id]
    approved = (action == "approve")
    
    await send_response_to_user(user_id, approved, order_id)
    
    status = "✅ APPROVED" if approved else "❌ REJECTED"
    admin_name = query.from_user.username or query.from_user.first_name
    
    updated_text = (
        f"🔍 APPROVAL REQUEST - {status} by @{admin_name}\n\n"
        f"👤 User: {user_info['username']}\n"
        f"🆔 User ID: {user_id}\n"
        f"📦 Order ID: {order_id}\n"
        f"📱 UDID: {user_info['udid']}\n"
        f"💳 Payment: ${user_info['payment_option']}\n"
        f"⏰ Submitted: {user_info['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}\n"
    )
    
    await query.edit_message_text(text=updated_text, reply_markup=None)
    
    del pending_approvals[user_id]
    if approved:
        if user_id in user_data:
            del user_data[user_id]
    
    logger.info(f"Admin @{admin_name} processed approval for order {order_id} (user {user_id}): {approved}")

async def handle_other_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message or not update.message.text: return
    text = update.message.text.strip()
    if 'start' in text.lower():
        await start(update, context)
        return
    await handle_udid_input(update, context)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Update {update} caused error {context.error}", exc_info=context.error)

async def main() -> None:
    print("🤖 Starting Enhanced Telegram Bot System...")
    print("=" * 50)
    
    try:
        app1 = Application.builder().token(BOT_TOKEN).build()
        app2 = Application.builder().token(BOT_2_TOKEN).build()
    except InvalidToken as e:
        logger.critical(f"Bot initialization failed due to invalid token: {e}", exc_info=True)
        return

    app1.add_error_handler(error_handler)
    app2.add_error_handler(error_handler)
    
    app1.add_handler(CommandHandler("start", start))
    app1.add_handler(CommandHandler("Details", details_order))
    app1.add_handler(CommandHandler("details", details_order))
    app1.add_handler(CallbackQueryHandler(handle_payment_button, pattern='^payment_'))
    app1.add_handler(MessageHandler(filters.PHOTO, handle_screenshot))
    app1.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_other_messages))
    
    app2.add_handler(CallbackQueryHandler(handle_bot2_callback, pattern='^(approve|reject)_'))
    app2.add_handler(CallbackQueryHandler(handle_copy_udid, pattern='^copyudid_'))
    
    print("✅ Bot 1 (User Interface) configured")
    print("✅ Bot 2 (Admin Panel) configured")
    print("🚀 Starting both bots concurrently...")
    
    async with app1, app2:
        await app1.start()
        await app2.start()
        
        print("=" * 50)
        print("✅ Both bots are now running!")
        print("=" * 50)
        
        if app1.updater and app2.updater:
            await asyncio.gather(
                app1.updater.start_polling(allowed_updates=Update.ALL_TYPES),
                app2.updater.start_polling(allowed_updates=Update.ALL_TYPES)
            )
        else:
            logger.error("Updater not available for one or both bots.")
            return
        
        try:
            await asyncio.Future()
        except (KeyboardInterrupt, SystemExit):
            logger.info("Stop signal received. Shutting down bots...")
            print("\n🛑 Shutting down bots...")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.critical(f"Critical error in main execution: {e}", exc_info=True)