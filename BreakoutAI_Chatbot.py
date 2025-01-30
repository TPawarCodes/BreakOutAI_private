from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from PIL import Image
from urllib.parse import quote_plus
import uuid
import tempfile
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, ConversationHandler
from pymongo import MongoClient
import google.generativeai as genai
import requests
import json
import os
import fitz 
import requests
from telegram import Update, InputFile
import re
from telegram.helpers import escape_markdown
from googletrans import Translator, LANGUAGES
import random
import string
from datetime import datetime

username = quote_plus("YOUR_MONGO_DB_USERNAME")
password = quote_plus("YOUR_MONGO_DB_PASSWORD")

uri = "YOUR_MONGO_DB_URI"

client = MongoClient(uri, server_api=ServerApi('1'))
db = client["breakout_bot"]
users_collection = db["users"]
chats_collection = db["chats"]
files_collection = db["files"]
trans_collection = db["trans"]

genai.configure(api_key="YOUR_GEMINI_API_KEY")

app = Application.builder().token("YOUR_TELEGRAM_BOT").build()

referrals_collection = db["referrals"]

def generate_referral_code(length=8):
    """Generate a random referral code"""
    characters = string.ascii_uppercase + string.digits
    return ''.join(random.choice(characters) for _ in range(length))

async def start(update: Update, context: CallbackContext):
    user = update.message.from_user
    chat_id = user.id
    
    # Check if user came from a referral
    if context.args and context.args[0]:
        referral_code = context.args[0]
        referrer = users_collection.find_one({"referral_code": referral_code})
        
        if referrer and referrer['chat_id'] != chat_id:
            # Record the referral
            referrals_collection.insert_one({
                "referrer_id": referrer['chat_id'],
                "referred_id": chat_id,
                "date": datetime.now(),
                "status": "pending"  # Will be updated to "completed" after phone verification
            })

    existing_user = users_collection.find_one({"chat_id": chat_id})
    if not existing_user:
        # Generate unique referral code for new user
        referral_code = generate_referral_code()
        while users_collection.find_one({"referral_code": referral_code}):
            referral_code = generate_referral_code()
            
        users_collection.insert_one({
            "chat_id": chat_id,
            "first_name": user.first_name,
            "username": user.username,
            "phone": None,
            "referral_code": referral_code,
            "referral_count": 0,
            "rewards_earned": 0
        })

        keyboard = [[KeyboardButton("📱 Share Phone Number", request_contact=True)]]
        reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)

        await update.message.reply_text("Welcome! Please share your phone number.", reply_markup=reply_markup)
        
    else:
        await show_main_menu(update, context)

async def save_phone(update: Update, context: CallbackContext):
    contact = update.message.contact
    chat_id = update.message.chat_id
    
    users_collection.update_one(
        {"chat_id": chat_id}, 
        {"$set": {"phone": contact.phone_number}}
    )
    
    referral = referrals_collection.find_one({"referred_id": chat_id, "status": "pending"})
    if referral:
        
        referrals_collection.update_one(
            {"_id": referral["_id"]},
            {"$set": {"status": "completed"}}
        )
        
        
        users_collection.update_one(
            {"chat_id": referral["referrer_id"]},
            {
                "$inc": {
                    "referral_count": 1,
                    "rewards_earned": 10  
                }
            }
        )
        
        await context.bot.send_message(
            chat_id=referral["referrer_id"],
            text="Congratulations! Someone you referred has joined and verified their account. You've earned 10 reward points!"
        )

    await show_main_menu(update, context)

async def show_main_menu(update: Update, context: CallbackContext):
    user = users_collection.find_one({"chat_id": update.message.chat_id})
    referral_link = f"https://t.me/{context.bot.username}?start={user['referral_code']}"
    
    menu_text = f"""Welcome! 
Your Referral Code: {user['referral_code']}
Total Referrals: {user['referral_count']}
Reward Points: {user['rewards_earned']}

Share your referral link: {referral_link}

Please choose from the following:
1. Chat with Gemini - /AI_chat
2. Image Description - /Image_Analysis
3. Search the web - /Web_search
4. Summarize documents - /Document_Analysis
5. Translate text - /Translate
6. View Referral Status - /referral_status"""

    await update.message.reply_text(menu_text)

async def referral_status(update: Update, context: CallbackContext):
    chat_id = update.message.chat_id
    user = users_collection.find_one({"chat_id": chat_id})
    
    # Get all successful referrals
    successful_referrals = referrals_collection.find({
        "referrer_id": chat_id,
        "status": "completed"
    }).count()
    
    # Get pending referrals
    pending_referrals = referrals_collection.find({
        "referrer_id": chat_id,
        "status": "pending"
    }).count()
    
    status_text = f"""📊 Your Referral Status:
    
🔑 Your Referral Code: {user['referral_code']}
👥 Total Successful Referrals: {successful_referrals}
⏳ Pending Referrals: {pending_referrals}
🎁 Total Reward Points: {user['rewards_earned']}

Share your referral link:
https://t.me/{context.bot.username}?start={user['referral_code']}"""

    await update.message.reply_text(status_text)


async def AI_Chat(update: Update, context: CallbackContext):
    chat_id = update.message.chat_id

    await update.message.reply_text("Hi, How can I help you?")

    context.user_data["mode"] = "chat"

    return

async def handle_Chat(update: Update, context: CallbackContext):
    user_text = update.message.text
    query = user_text.strip()
    chat_id = update.message.chat_id

    if query.startswith("/"):
        context.user_data["convo"] = ""
        

    if context.user_data.get("mode") == "web_search":
        await handle_Web_Search(update, context)
        return
    if context.user_data.get("mode") == "translate":
        await handle_Translate(update, context)
        return
    
    translator = Translator()
    detected = await translator.detect(user_text)
    translated = await translator.translate(user_text, src='auto', dest='en')
    translated_text = translated.text
    

    instructions = "(Don't make any text BOLD, leave it in plain text. This is an API response, you are an AI assistant)"
    chat_model = genai.GenerativeModel("gemini-1.5-flash")
    convo = context.user_data.get("convo", "")
    response = chat_model.generate_content("Earlier Chat: " + instructions + "\n" + convo + "Current Query: " + translated_text)

    d = detected.lang
    translated_rep = await translator.translate(response.text, src='en', dest=d)
    translated_reply = translated_rep.text
    bot_reply = translated_reply
    chats_collection.insert_one({"chat_id": chat_id, "user_query": user_text, "bot_reply": bot_reply})

    convo += "\nUser: " + user_text + " Bot_reply: " + bot_reply
    context.user_data["convo"] = convo


    await update.message.reply_text(bot_reply)

async def Image_Analysis(update: Update, context: CallbackContext):
    chat_id = update.message.chat_id

    # Step 1: Send greeting immediately when user enters /AI_chat
    await update.message.reply_text("Please upload the Image as an attachment")

    return

async def handle_Image_Analysis(update: Update, context: CallbackContext):
    file = await update.message.document.get_file()
    
    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as temp_file:
        file_path = temp_file.name
        await file.download_to_drive(file_path)
        img  = Image.open(file_path)

    chat_model = genai.GenerativeModel("gemini-2.0-flash-exp")
    response = chat_model.generate_content(["Analyze this file:", img], stream=True, generation_config= genai.types.GenerationConfig(temperature=0.8))

    response.resolve()

    files_collection.insert_one({
        "chat_id": update.message.chat_id,
        "filename": update.message.document.file_name,
        "description": response.text
    })

    await update.message.reply_text(f"File analyzed: {response.text}")

async def Web_Search(update: Update, context: CallbackContext):
    chat_id = update.message.chat_id
    
    context.user_data["mode"] = "web_search"

    await update.message.reply_text("Please enter your query for AI-powered web search.")

    return

async def handle_Web_Search(update: Update, context: CallbackContext):

    query = update.message.text.strip()

    if query.startswith("/"):
        context.user_data["mode"] = None
        return
        
    if not query:
        await update.message.reply_text("Usage: /websearch <your query>")
        return

    search_url = f"https://www.googleapis.com/customsearch/v1?q={query}&key=YOUR_GOOGLE_API_KEY&cx=YOUR_CX"
    response = requests.get(search_url).json()
    
    results = response.get("items", [])[:3]  # Get top 3 results
    search_summary = "\n\n".join([f"{r['title']}\n{r['link']}" for r in results])
    await update.message.reply_text(search_summary or "No results found.")

# Helper function to escape MarkdownV2 special characters
    
    def escape_markdown_v2(text: str) -> str:
        return re.sub(r'([._*{}\[\]()#+\-.!=|])', r'\\\1', text)

    chat_model = genai.GenerativeModel("gemini-2.0-flash-exp")
    response = chat_model.generate_content(f"Dont make any text bold. Summarize the following search results: {results}")

    # Store search in MongoDB
    chats_collection.insert_one({
        "chat_id": update.message.chat_id,
        "user_query": query,
        "search_results": results,
        "ai_summary": response.text
    })

    # Reply with AI-generated summary and links
    summary_message = escape_markdown_v2(f"AI Summary:\n{response.text}\n\nTop Links:\n{search_summary}")
    await update.message.reply_text(summary_message, parse_mode="MarkdownV2")

async def Document_Analysis(update: Update, context: CallbackContext):
    chat_id = update.message.chat_id
    
    await update.message.reply_text("Please upload the document.")

    return

async def handle_document(update: Update, context: CallbackContext):
    file = update.message.document  # Handle documents
    file_id = file.file_id
    file_name = file.file_name

    # Get the file from Telegram servers
    new_file = await context.bot.get_file(file_id)

    # Save the file temporarily
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_file:
        file_path = temp_file.name
        await new_file.download_to_drive(custom_path=file_path)

    # Function to extract text from PDF
    def extract_text_from_pdf(pdf_path):
        text = ""
        with fitz.open(pdf_path) as doc:
            for page in doc:
                text += page.get_text()
        return text if text else "No extractable text found."

    extracted_text = extract_text_from_pdf(file_path)
    
    # AI Analysis using Gemini
    chat_model = genai.GenerativeModel("gemini-2.0-flash-exp")
    response = chat_model.generate_content(f"Describe the following document (In less than 4000 characters) : {extracted_text}")
    description = response.text

    # Save metadata in MongoDB
    files_collection.insert_one({
        "chat_id": update.message.chat_id,
        "file_name": file_name,
        "description": description
    })

    # **Function to split message into chunks (4096 chars max)**
    def split_message(text, max_length=4000):
        return [text[i:i+max_length] for i in range(0, len(text), max_length)]

    # Reply with AI analysis
    def escape_markdown_v2(text: str) -> str:
        return re.sub(r'([._*{}\[\]()#+\-.!=|])', r'\\\1', text)

    summary_message = escaped_description = escape_markdown(description, version=2)
    message_parts = split_message(summary_message)
    await update.message.reply_text(summary_message, parse_mode="MarkdownV2")
    #await update.message.reply_text("Document Analysis:**", parse_mode="MarkdownV2")
    #for part in message_parts:
    #    await update.message.reply_text(part, parse_mode="MarkdownV2")


async def Translate(update: Update, context: CallbackContext):
    chat_id = update.message.chat_id

    # Ask for output language
    langs  = ""
    for langcode,langname in LANGUAGES.items():
        langs+= f"\n{langcode}: {langname}"

    await update.message.reply_text(f"{langs}\nPlease enter the output language (e.g., 'en' for English, 'fr' for French)")

    # Store that the user is in the translation mode
    context.user_data["awaiting"] = "output_language"
    context.user_data["mode"] = "translate"
    
    return

async def handle_Translate(update: Update, context: CallbackContext):
    user_text = update.message.text
    query = user_text.strip()
    chat_id = update.message.chat_id

    if query.startswith("/"):
        context.user_data["mode"] = None
        return

    # Check if we are waiting for the output language
    if "awaiting" in context.user_data and context.user_data["awaiting"] == "output_language":
        context.user_data["output_language"] = user_text  # Store the output language
        await update.message.reply_text("Please enter the text you want to translate:")

        # Update the state to waiting for user text input
        context.user_data["awaiting"] = "text_to_translate"
        return

    if "awaiting" in context.user_data and context.user_data["awaiting"] == "text_to_translate":
        output_language = context.user_data.get("output_language", "en")  # Default to English if no language is set
        user_text = update.message.text

        # Translate text using googletrans
        translator = Translator()
        translation = await translator.translate(text=user_text, src='auto', dest=output_language)

        # Extract translated text
        bot_reply = translation.text

        # Store chat data in MongoDB
        trans_collection.insert_one({
            "chat_id": chat_id,
            "user_query": user_text,
            "bot_reply": bot_reply
        })

        # Send the translated text back to the user
        await update.message.reply_text(bot_reply)

        return

async def Menu(update: Update, context: CallbackContext):
    chat_id = update.message.chat_id

    await update.message.reply_text("""Choose from the following:
1. Chat with Gemini - /AI_chat
2. Image Description - /Image_Analysis
3. Search the web - /Web_search
4. Summarize documents - /Document_Analysis
5. Translate text - /Translate
6. View Referral Status - /referral_status""")

    return

def main():
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.CONTACT, save_phone))
    app.add_handler(CommandHandler("AI_chat", AI_Chat))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_Chat))
    app.add_handler(CommandHandler("Image_Analysis", Image_Analysis))  
    app.add_handler(MessageHandler(filters.Document.IMAGE, handle_Image_Analysis))
    app.add_handler(CommandHandler("Web_Search", Web_Search))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_Web_Search))
    app.add_handler(CommandHandler("Document_Analysis", Document_Analysis))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(CommandHandler("Translate", Translate))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_Translate))
    app.add_handler(CommandHandler("referral_status", referral_status))
    app.add_handler(CommandHandler("menu", Menu))


    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()

