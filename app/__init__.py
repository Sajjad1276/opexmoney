# Install global Telegram warning auto-cleanup before handlers start sending messages.
from app.utils.temporary_messages import install_warning_auto_delete

install_warning_auto_delete()
