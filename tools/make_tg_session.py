"""Make a Telegram login (session string) for the reader account. Run on your own computer:
    pip install telethon
    python tools/make_tg_session.py
Enter api_id and api_hash from https://my.telegram.org, the reader account's phone number and the code Telegram sends.
Put the printed session string into the GitHub secret TG_SESSION. Never share it: it gives full access to that account."""
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = int(input("api_id: "))
api_hash = input("api_hash: ").strip()
with TelegramClient(StringSession(), api_id, api_hash) as client:
    print("\nChannels this account can read:")
    for d in client.get_dialogs():
        if d.is_channel:
            print(f"  {d.id}  {d.name}")
    print("\nSession string (secret, goes into GitHub as TG_SESSION):\n")
    print(client.session.save())
