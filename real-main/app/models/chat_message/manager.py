import collections
import logging
import uuid

import pendulum

from app import models
from app.mixins.base import ManagerBase
from app.mixins.view.manager import ViewManagerMixin
from app.models.card.specs import ChatCardSpec

from . import exceptions
from .appsync import ChatMessageAppSync
from .dynamo import ChatMessageDynamo
from .model import ChatMessage

logger = logging.getLogger()


class ChatMessageManager(ViewManagerMixin, ManagerBase):

    exceptions = exceptions
    item_type = 'chatMessage'

    def __init__(self, clients, managers=None):
        super().__init__(clients, managers=managers)
        managers = managers or {}
        managers['chat_message'] = self
        self.block_manager = managers.get('block') or models.BlockManager(clients, managers=managers)
        self.card_manager = managers.get('card') or models.CardManager(clients, managers=managers)
        self.chat_manager = managers.get('chat') or models.ChatManager(clients, managers=managers)
        self.user_manager = managers.get('user') or models.UserManager(clients, managers=managers)

        self.clients = clients
        if 'appsync' in clients:
            self.appsync = ChatMessageAppSync(clients['appsync'])
        if 'dynamo' in clients:
            self.dynamo = ChatMessageDynamo(clients['dynamo'])

    def get_chat_message(self, message_id, strongly_consistent=False):
        item = self.dynamo.get_chat_message(message_id, strongly_consistent=strongly_consistent)
        return self.init_chat_message(item) if item else None

    def init_chat_message(self, item):
        kwargs = {
            'chat_message_appsync': self.appsync,
            'chat_message_dynamo': self.dynamo,
            'view_dynamo': getattr(self, 'view_dynamo', None),
            'block_manager': self.block_manager,
            'chat_manager': self.chat_manager,
            'user_manager': self.user_manager,
        }
        return ChatMessage(item, **kwargs)

    def postprocess_record(self, pk, sk, old_item, new_item):
        # message added
        if not old_item and new_item:  # message added
            chat_id = new_item['chatId']['S']
            user_id = new_item.get('userId', {}).get('S')  # system messages have no userId
            created_at = pendulum.parse(new_item['createdAt']['S'])
            chat = self.chat_manager.get_chat(chat_id)
            if chat:
                chat.dynamo.increment_message_count(chat_id)
                chat.update_last_message_activity_at(user_id, created_at)

        # message edited
        if old_item and new_item:
            chat_id = new_item['chatId']['S']
            user_id = new_item['userId']['S']
            edited_at = pendulum.parse(new_item['lastEditedAt']['S'])
            chat = self.chat_manager.get_chat(chat_id)
            if chat:
                chat.update_last_message_activity_at(user_id, edited_at)

        # message deleted
        if old_item and not new_item:
            chat_id = old_item['chatId']['S']
            chat = self.chat_manager.get_chat(chat_id)
            if chat:
                chat.dynamo.decrement_message_count(chat_id)

    def add_chat_message(self, message_id, text, chat_id, user_id, now=None):
        now = now or pendulum.now('utc')
        text_tags = self.user_manager.get_text_tags(text)
        item = self.dynamo.add_chat_message(message_id, chat_id, user_id, text, text_tags, now)
        return self.init_chat_message(item)

    def truncate_chat_messages(self, chat_id):
        # delete all chat messages for the chat
        with self.dynamo.client.table.batch_writer() as batch:
            for chat_message_pk in self.dynamo.generate_chat_messages_by_chat(chat_id, pks_only=True):
                chat_message_id = chat_message_pk['partitionKey'].split('/')[1]
                for view_pk in self.view_dynamo.generate_views(chat_message_id, pks_only=True):
                    batch.delete_item(Key=view_pk)
                batch.delete_item(Key=chat_message_pk)

    def add_system_message_group_created(self, chat_id, created_by_user, name=None, now=None):
        text = f'@{created_by_user.username} created the group'
        if name:
            text += f' "{name}"'
        return self.add_system_message(chat_id, text, user_ids=[created_by_user.id], now=now)

    def add_system_message_added_to_group(self, chat_id, added_by_user, users, now=None):
        assert users, 'No system message should be sent if no users added to group'
        text = f'@{added_by_user.username} added '
        user_1 = users.pop()
        if users:
            text += ', '.join(f'@{u.username}' for u in users)
            text += ' and '
        text += f'@{user_1.username} to the group'
        return self.add_system_message(chat_id, text, user_ids=[u.id for u in users], now=now)

    def add_system_message_left_group(self, chat_id, user):
        text = f'@{user.username} left the group'
        return self.add_system_message(chat_id, text)

    def add_system_message_group_name_edited(self, chat_id, changed_by_user, new_name):
        text = f'@{changed_by_user.username} '
        if new_name:
            text += f'changed the name of the group to "{new_name}"'
        else:
            text += 'deleted the name of the group'
        return self.add_system_message(chat_id, text)

    def add_system_message(self, chat_id, text, user_ids=None, now=None):
        user_id = None
        message_id = str(uuid.uuid4())
        message = self.add_chat_message(message_id, text, chat_id, user_id, now=now)
        message.trigger_notifications(message.enums.ChatMessageNotificationType.ADDED, user_ids=user_ids)
        return message

    def record_views(self, message_ids, user_id, viewed_at=None):
        grouped_message_ids = dict(collections.Counter(message_ids))
        if not grouped_message_ids:
            return

        views_recorded = False
        for message_id, view_count in grouped_message_ids.items():
            message = self.get_chat_message(message_id)
            if not message:
                logger.warning(f'Cannot record view(s) by user `{user_id}` on DNE message `{message_id}`')
                continue
            if message.record_view_count(user_id, view_count, viewed_at=viewed_at):
                views_recorded = True
        if views_recorded:
            self.card_manager.remove_card_by_spec_if_exists(ChatCardSpec(user_id))
