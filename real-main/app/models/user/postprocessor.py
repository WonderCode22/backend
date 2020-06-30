import logging

from app.models.card.specs import RequestedFollowersCardSpec

from .enums import UserStatus

logger = logging.getLogger()


class UserPostProcessor:
    def __init__(self, elasticsearch_client=None, pinpoint_client=None, card_manager=None):
        self.elasticsearch_client = elasticsearch_client
        self.pinpoint_client = pinpoint_client
        self.card_manager = card_manager

    def run(self, pk, sk, old_item, new_item):
        user_id = pk[len('user/') :]
        self.handle_elasticsearch(old_item, new_item)
        self.handle_pinpoint(user_id, old_item, new_item)
        self.handle_requested_followers_card(user_id, old_item, new_item)

    def handle_elasticsearch(self, old_item, new_item):
        user_id = (new_item or old_item)['userId']
        # if we're manually rebuilding the index, treat everything as new
        new_reindexed_at = new_item.get('lastManuallyReindexedAt')
        old_reindexed_at = old_item.get('lastManuallyReindexedAt')
        if new_reindexed_at and new_reindexed_at != old_reindexed_at:
            old_item = {}

        if new_item and old_item:
            self.elasticsearch_client.update_user(user_id, old_item, new_item)
        if new_item and not old_item:
            self.elasticsearch_client.add_user(user_id, new_item)
        if not new_item and old_item:
            self.elasticsearch_client.delete_user(user_id)

    def handle_pinpoint(self, user_id, old_item, new_item):
        # check if this was a user deletion
        if old_item and not new_item:
            self.pinpoint_client.delete_user_endpoints(user_id)
            return

        # check for a change of email, phone
        for dynamo_name, pinpoint_name in (('email', 'EMAIL'), ('phoneNumber', 'SMS')):
            value = new_item.get(dynamo_name)
            if old_item.get(dynamo_name) == value:
                continue
            if value:
                self.pinpoint_client.update_user_endpoint(user_id, pinpoint_name, value)
            else:
                self.pinpoint_client.delete_user_endpoint(user_id, pinpoint_name)

        # check if this was a change in user status
        status = new_item.get('userStatus', UserStatus.ACTIVE)
        if old_item and old_item.get('userStatus', UserStatus.ACTIVE) != status:
            if status == UserStatus.ACTIVE:
                self.pinpoint_client.enable_user_endpoints(user_id)
            if status == UserStatus.DISABLED:
                self.pinpoint_client.disable_user_endpoints(user_id)
            if status == UserStatus.DELETING:
                self.pinpoint_client.delete_user_endpoints(user_id)

    def handle_requested_followers_card(self, user_id, old_item, new_item):
        old_requested_followers_count = (old_item or {}).get('followersRequestedCount', 0)
        new_requested_followers_count = (new_item or {}).get('followersRequestedCount', 0)
        card_spec = RequestedFollowersCardSpec(user_id)

        if old_requested_followers_count == 0 and new_requested_followers_count > 0:
            self.card_manager.add_card_by_spec_if_dne(card_spec)

        if old_requested_followers_count > 0 and new_requested_followers_count == 0:
            self.card_manager.remove_card_by_spec_if_exists(card_spec)