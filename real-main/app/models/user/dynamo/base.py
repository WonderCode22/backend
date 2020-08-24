import collections
import logging

import pendulum
from boto3.dynamodb.conditions import Key

from ..enums import UserPrivacyStatus, UserStatus, UserSubscriptionLevel
from ..exceptions import UserAlreadyExists, UserAlreadyGrantedSubscription

logger = logging.getLogger()


class UserDynamo:
    def __init__(self, dynamo_client):
        self.client = dynamo_client

    def pk(self, user_id):
        return {
            'partitionKey': f'user/{user_id}',
            'sortKey': 'profile',
        }

    def parse_pk(self, pk):
        return pk['partitionKey'].split('/')[1]

    def get_user(self, user_id, strongly_consistent=False):
        return self.client.get_item(self.pk(user_id), ConsistentRead=strongly_consistent)

    def get_user_by_username(self, username):
        query_kwargs = {
            'KeyConditionExpression': Key('gsiA1PartitionKey').eq(f'username/{username}'),
            'IndexName': 'GSI-A1',
        }
        return self.client.query_head(query_kwargs)

    def delete_user(self, user_id):
        return self.client.delete_item(self.pk(user_id))

    def add_user(self, user_id, username, full_name=None, gender=None, birthDate=None, email=None, phone=None,
                 placeholder_photo_code=None, now=None):

        now = now or pendulum.now('utc')
        query_kwargs = {
            'Item': {
                'schemaVersion': 11,
                'partitionKey': f'user/{user_id}',
                'sortKey': 'profile',
                'gsiA1PartitionKey': f'username/{username}',
                'gsiA1SortKey': '-',
                'userId': user_id,
                'username': username,
                'privacyStatus': UserPrivacyStatus.PUBLIC,
                'signedUpAt': now.to_iso8601_string(),
            },
        }
        if full_name:
            query_kwargs['Item']['fullName'] = full_name
        if placeholder_photo_code:
            query_kwargs['Item']['placeholderPhotoCode'] = placeholder_photo_code
        if email:
            query_kwargs['Item']['email'] = email
        if phone:
            query_kwargs['Item']['phoneNumber'] = phone
        if gender:
            query_kwargs['Item']['gender'] = phone
        if birthDate:
            query_kwargs['Item']['birthDate'] = phone
        try:
            return self.client.add_item(query_kwargs)
        except self.client.exceptions.ConditionalCheckFailedException:
            raise UserAlreadyExists(user_id)

    def update_user_username(self, user_id, username, old_username, now=None):
        now = now or pendulum.now('utc')
        query_kwargs = {
            'Key': self.pk(user_id),
            'UpdateExpression': 'SET '
            + ', '.join(
                [
                    'username = :un',
                    'usernameLastValue = :oldun',
                    'usernameLastChangedAt = :ulca',
                    'gsiA1PartitionKey = :gsia1pk',
                ]
            ),
            'ExpressionAttributeValues': {
                ':un': username,
                ':oldun': old_username,
                ':ulca': now.to_iso8601_string(),
                ':gsia1pk': f'username/{username}',
            },
            'ConditionExpression': 'username = :oldun',
        }
        return self.client.update_item(query_kwargs)

    def set_user_photo_post_id(self, user_id, photo_id):
        query_kwargs = {
            'Key': self.pk(user_id),
        }

        if photo_id:
            query_kwargs['UpdateExpression'] = 'SET photoPostId = :ppid'
            query_kwargs['ExpressionAttributeValues'] = {':ppid': photo_id}
        else:
            query_kwargs['UpdateExpression'] = 'REMOVE photoPostId'

        return self.client.update_item(query_kwargs)

    def set_user_status(self, user_id, status, now=None):
        assert status in UserStatus._ALL, f'Invalid UserStatus `{status}`'
        now = now or pendulum.now('utc')
        query_kwargs = {
            'Key': self.pk(user_id),
        }
        if status == UserStatus.ACTIVE:  # default value
            query_kwargs['UpdateExpression'] = 'REMOVE userStatus'
        else:
            query_kwargs['UpdateExpression'] = 'SET userStatus = :s'
            query_kwargs['ExpressionAttributeValues'] = {':s': status}
        if status == UserStatus.DISABLED:
            query_kwargs['UpdateExpression'] += ', lastDisabledAt = :lda'
            query_kwargs['ExpressionAttributeValues'][':lda'] = now.to_iso8601_string()
        return self.client.update_item(query_kwargs)

    def set_user_privacy_status(self, user_id, privacy_status):
        assert privacy_status in UserPrivacyStatus._ALL, f'Invalid privacy_status `{privacy_status}`'
        query_kwargs = {
            'Key': self.pk(user_id),
            'UpdateExpression': 'SET privacyStatus = :ps',
            'ExpressionAttributeValues': {':ps': privacy_status},
        }
        return self.client.update_item(query_kwargs)

    def set_user_details(
        self,
        user_id,
        full_name=None,
        bio=None,
        gender=None,
        birthDate=None,
        language_code=None,
        theme_code=None,
        follow_counts_hidden=None,
        view_counts_hidden=None,
        email=None,
        phone=None,
        comments_disabled=None,
        likes_disabled=None,
        sharing_disabled=None,
        verification_hidden=None,
    ):
        "To ignore an attribute, leave it set to None. To delete an attribute, set it to the empty string."
        expression_actions = collections.defaultdict(list)
        expression_attribute_values = {}

        def process_attr(name, value):
            if value is not None:
                if value != '':
                    expression_actions['SET'].append(f'{name} = :{name}')
                    expression_attribute_values[f':{name}'] = value
                else:
                    expression_actions['REMOVE'].append(name)

        process_attr('fullName', full_name)
        process_attr('bio', bio)
        process_attr('gender', gender)
        process_attr('birthDate', birthDate)
        process_attr('languageCode', language_code)
        process_attr('themeCode', theme_code)
        process_attr('followCountsHidden', follow_counts_hidden)
        process_attr('viewCountsHidden', view_counts_hidden)
        process_attr('email', email)
        process_attr('phoneNumber', phone)
        process_attr('commentsDisabled', comments_disabled)
        process_attr('likesDisabled', likes_disabled)
        process_attr('sharingDisabled', sharing_disabled)
        process_attr('verificationHidden', verification_hidden)

        query_kwargs = {
            'Key': self.pk(user_id),
            'UpdateExpression': ' '.join([f'{k} {", ".join(v)}' for k, v in expression_actions.items()]),
        }
        if expression_attribute_values:
            query_kwargs['ExpressionAttributeValues'] = expression_attribute_values

        return self.client.update_item(query_kwargs)

    def set_user_accepted_eula_version(self, user_id, version):
        query_kwargs = {
            'Key': self.pk(user_id),
        }
        if version is None:
            query_kwargs['UpdateExpression'] = 'REMOVE acceptedEULAVersion'
        else:
            query_kwargs['UpdateExpression'] = 'SET acceptedEULAVersion = :aev'
            query_kwargs['ExpressionAttributeValues'] = {':aev': version}
        return self.client.update_item(query_kwargs)

    def grant_subscription(self, user_id, sub_level, sub_granted_at, sub_expires_at):
        assert sub_level != UserSubscriptionLevel.BASIC, "Cannot grant BASIC subscriptions"
        assert sub_expires_at, "Subscription grants must expire"
        query_kwargs = {
            'Key': self.pk(user_id),
            'UpdateExpression': 'SET #sl = :sl, #sga = :sga, #sea = :sea, #gsipk = :gsipk, #gsisk = :sea',
            'ConditionExpression': 'attribute_not_exists(#sga)',  # each user gets max one subscription grant
            'ExpressionAttributeNames': {
                '#sl': 'subscriptionLevel',
                '#sea': 'subscriptionExpiresAt',
                '#sga': 'subscriptionGrantedAt',
                '#gsipk': 'gsiK1PartitionKey',
                '#gsisk': 'gsiK1SortKey',
            },
            'ExpressionAttributeValues': {
                ':sl': sub_level,
                ':sga': sub_granted_at.to_iso8601_string(),
                ':sea': sub_expires_at.to_iso8601_string(),
                ':gsipk': f'user/{sub_level}',
            },
        }
        try:
            return self.client.update_item(query_kwargs)
        except self.client.exceptions.ConditionalCheckFailedException:
            # this improperly also catches the condition where the user item doesn't exist because
            # there is no way for us to know which conditional check clause failed
            raise UserAlreadyGrantedSubscription(user_id)

    def clear_subscription(self, user_id):
        # delete any active subscription, but leave the subscriptionGrantedAt if it exists
        # to record that this user has used up their free subscription bonus
        query_kwargs = {
            'Key': self.pk(user_id),
            'UpdateExpression': 'REMOVE #sl, #sea, #gsipk, #gsisk',
            'ExpressionAttributeNames': {
                '#sl': 'subscriptionLevel',
                '#sea': 'subscriptionExpiresAt',
                '#gsipk': 'gsiK1PartitionKey',
                '#gsisk': 'gsiK1SortKey',
            },
        }
        return self.client.update_item(query_kwargs)

    def generate_user_ids_by_subscription_level(self, sub_level, max_expires_at=None):
        assert sub_level != UserSubscriptionLevel.BASIC, "Cannot generate for BASIC subscriptions"
        query_kwargs = {
            'KeyConditionExpression': 'gsiK1PartitionKey = :gsipk',
            'ProjectionExpression': 'partitionKey',
            'ExpressionAttributeValues': {':gsipk': f'user/{sub_level}'},
            'IndexName': 'GSI-K1',
        }
        if max_expires_at:
            query_kwargs['KeyConditionExpression'] += ' AND gsiK1SortKey <= :mea'
            query_kwargs['ExpressionAttributeValues'][':mea'] = max_expires_at.to_iso8601_string()
        return (key['partitionKey'].split('/')[1] for key in self.client.generate_all_query(query_kwargs))

    def increment_album_count(self, user_id):
        return self.client.increment_count(self.pk(user_id), 'albumCount')

    def decrement_album_count(self, user_id):
        return self.client.decrement_count(self.pk(user_id), 'albumCount')

    def increment_card_count(self, user_id):
        return self.client.increment_count(self.pk(user_id), 'cardCount')

    def decrement_card_count(self, user_id):
        return self.client.decrement_count(self.pk(user_id), 'cardCount')

    def increment_chat_count(self, user_id):
        return self.client.increment_count(self.pk(user_id), 'chatCount')

    def decrement_chat_count(self, user_id):
        return self.client.decrement_count(self.pk(user_id), 'chatCount')

    def increment_chat_messages_creation_count(self, user_id):
        return self.client.increment_count(self.pk(user_id), 'chatMessagesCreationCount')

    def increment_chat_messages_deletion_count(self, user_id):
        return self.client.increment_count(self.pk(user_id), 'chatMessagesDeletionCount')

    def increment_chat_messages_forced_deletion_count(self, user_id):
        return self.client.increment_count(self.pk(user_id), 'chatMessagesForcedDeletionCount')

    def increment_chats_with_unviewed_messages_count(self, user_id):
        return self.client.increment_count(self.pk(user_id), 'chatsWithUnviewedMessagesCount')

    def decrement_chats_with_unviewed_messages_count(self, user_id):
        return self.client.decrement_count(self.pk(user_id), 'chatsWithUnviewedMessagesCount')

    def increment_comment_count(self, user_id):
        return self.client.increment_count(self.pk(user_id), 'commentCount')

    def decrement_comment_count(self, user_id):
        return self.client.decrement_count(self.pk(user_id), 'commentCount')

    def increment_comment_deleted_count(self, user_id):
        return self.client.increment_count(self.pk(user_id), 'commentDeletedCount')

    def increment_comment_forced_deletion_count(self, user_id):
        return self.client.increment_count(self.pk(user_id), 'commentForcedDeletionCount')

    def increment_followed_count(self, user_id):
        return self.client.increment_count(self.pk(user_id), 'followedCount')

    def decrement_followed_count(self, user_id):
        return self.client.decrement_count(self.pk(user_id), 'followedCount')

    def increment_follower_count(self, user_id):
        return self.client.increment_count(self.pk(user_id), 'followerCount')

    def decrement_follower_count(self, user_id):
        return self.client.decrement_count(self.pk(user_id), 'followerCount')

    def increment_followers_requested_count(self, user_id):
        return self.client.increment_count(self.pk(user_id), 'followersRequestedCount')

    def decrement_followers_requested_count(self, user_id):
        return self.client.decrement_count(self.pk(user_id), 'followersRequestedCount')

    def increment_post_count(self, user_id):
        return self.client.increment_count(self.pk(user_id), 'postCount')

    def decrement_post_count(self, user_id):
        return self.client.decrement_count(self.pk(user_id), 'postCount')

    def increment_post_archived_count(self, user_id):
        return self.client.increment_count(self.pk(user_id), 'postArchivedCount')

    def decrement_post_archived_count(self, user_id):
        return self.client.decrement_count(self.pk(user_id), 'postArchivedCount')

    def increment_post_deleted_count(self, user_id):
        return self.client.increment_count(self.pk(user_id), 'postDeletedCount')

    def increment_post_forced_archiving_count(self, user_id):
        return self.client.increment_count(self.pk(user_id), 'postForcedArchivingCount')

    def increment_post_viewed_by_count(self, user_id):
        return self.client.increment_count(self.pk(user_id), 'postViewedByCount')
