import logging
import os

import pendulum
import stringcase

from app.mixins.trending.model import TrendingModelMixin
from app.models.post.enums import PostStatus, PostType
from app.utils import image_size

from .enums import UserPrivacyStatus, UserStatus, UserSubscriptionLevel
from .exceptions import UserException, UserValidationException, UserVerificationException
from .validate import UserValidate

logger = logging.getLogger()

S3_PLACEHOLDER_PHOTOS_DIRECTORY = os.environ.get('S3_PLACEHOLDER_PHOTOS_DIRECTORY')

# annoying this needs to exist
CONTACT_ATTRIBUTE_NAMES = {
    'email': {'short': 'email', 'cognito': 'email', 'dynamo': 'email'},
    'phone': {'short': 'phone', 'cognito': 'phone_number', 'dynamo': 'phoneNumber'},
}


class User(TrendingModelMixin):

    client_names = ['cloudfront', 'cognito', 'elasticsearch', 'dynamo', 'pinpoint', 's3_uploads']
    item_type = 'user'
    subscription_bonus_duration = pendulum.duration(months=3)

    def __init__(
        self,
        user_item,
        clients,
        dynamo=None,
        album_manager=None,
        block_manager=None,
        chat_manager=None,
        comment_manager=None,
        follower_manager=None,
        like_manager=None,
        post_manager=None,
        placeholder_photos_directory=S3_PLACEHOLDER_PHOTOS_DIRECTORY,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.clients = clients
        for client_name in self.client_names:
            if client_name in clients:
                setattr(self, f'{client_name}_client', clients[client_name])
        if dynamo:
            self.dynamo = dynamo
        if album_manager:
            self.album_manager = album_manager
        if block_manager:
            self.block_manager = block_manager
        if chat_manager:
            self.chat_manager = chat_manager
        if comment_manager:
            self.comment_manager = comment_manager
        if follower_manager:
            self.follower_manager = follower_manager
        if like_manager:
            self.like_manager = like_manager
        if post_manager:
            self.post_manager = post_manager
        self.validate = UserValidate()
        self.item = user_item
        self.id = user_item['userId']
        self.placeholder_photos_directory = placeholder_photos_directory

    @property
    def username(self):
        return self.item['username']

    @property
    def status(self):
        return self.item.get('userStatus', UserStatus.ACTIVE)

    def get_photo_path(self, size, photo_post_id=None):
        photo_post_id = photo_post_id or self.item.get('photoPostId')
        if not photo_post_id:
            return None
        return '/'.join([self.id, 'profile-photo', photo_post_id, size.filename])

    def get_placeholder_photo_path(self, size):
        code = self.item.get('placeholderPhotoCode')
        if not code or not self.placeholder_photos_directory:
            return None
        return '/'.join([self.placeholder_photos_directory, code, size.filename])

    def get_photo_url(self, size):
        photo_path = self.get_photo_path(size)
        if photo_path:
            return self.cloudfront_client.generate_presigned_url(photo_path, ['GET', 'HEAD'])
        return None

    def is_forced_disabling_criteria_met_by_chat_messages(self):
        # matching post criteria
        total_count = self.item.get('chatMessagesCreationCount', 0)
        forced_deleted_count = self.item.get('chatMessagesForcedDeletionCount', 0)
        return total_count > 5 and forced_deleted_count > total_count / 10

    def is_forced_disabling_criteria_met_by_comments(self):
        # matching post criteria
        total_comment_count = self.item.get('commentCount', 0) + self.item.get('commentDeletedCount', 0)
        forced_deleted_count = self.item.get('commentForcedDeletionCount', 0)
        return total_comment_count > 5 and forced_deleted_count > total_comment_count / 10

    def is_forced_disabling_criteria_met_by_posts(self):
        # forced disabling criteria, (directly from spec):
        #   - user has over 5 posts
        #   - their forced post archivings is at least 10% of their total post count
        total_post_count = self.item.get('postCount', 0) + self.item.get('postArchivedCount', 0)
        forced_archiving_count = self.item.get('postForcedArchivingCount', 0)
        return total_post_count > 5 and forced_archiving_count > total_post_count / 10

    def refresh_item(self, strongly_consistent=False):
        self.item = self.dynamo.get_user(self.id, strongly_consistent=strongly_consistent)
        return self

    def serialize(self, caller_user_id):
        assert self.item
        resp = self.item.copy()
        resp['blockerStatus'] = self.block_manager.get_block_status(self.id, caller_user_id)
        resp['followedStatus'] = self.follower_manager.get_follow_status(caller_user_id, self.id)
        return resp

    def enable(self):
        if self.status == UserStatus.ACTIVE:
            pass
        elif self.status == UserStatus.DISABLED:
            self.item = self.dynamo.set_user_status(self.id, UserStatus.ACTIVE)
        elif self.status == UserStatus.DELETING:
            raise UserException(f'Cannot enable user `{self.id}` in status `{self.status}`')
        else:
            raise Exception(f'Unrecognized user status `{self.status}`')
        return self

    def disable(self, forced_by=None):
        if self.status == UserStatus.ACTIVE:
            self.item = self.dynamo.set_user_status(self.id, UserStatus.DISABLED)
            if forced_by:
                # the string USER_FORCE_DISABLED is hooked up to a cloudwatch metric & alert
                logger.warning(
                    f'USER_FORCE_DISABLED: user `{self.id}` / `{self.username}` disabled due to {forced_by}'
                )
        elif self.status == UserStatus.DISABLED:
            pass
        elif self.status == UserStatus.DELETING:
            raise UserException(f'Cannot disable user `{self.id}` in status `{self.status}`')
        else:
            raise Exception(f'Unrecognized user status `{self.status}`')
        return self

    def delete(self, skip_cognito=False):
        if self.status != UserStatus.DELETING:
            self.item = self.dynamo.set_user_status(self.id, UserStatus.DELETING)

        # for REQUESTED and DENIED, just delete them
        # for FOLLOWING, unfollow so that the other user's counts remain correct
        self.follower_manager.reset_followed_items(self.id)
        self.follower_manager.reset_follower_items(self.id)

        # unflag everything we've flagged
        self.post_manager.unflag_all_by_user(self.id)
        self.comment_manager.unflag_all_by_user(self.id)

        # delete all our likes & comments & albums & posts
        self.like_manager.dislike_all_by_user(self.id)
        self.comment_manager.delete_all_by_user(self.id)
        self.album_manager.delete_all_by_user(self.id)
        self.post_manager.delete_all_by_user(self.id)

        # remove all blocks of and by us
        self.block_manager.unblock_all_blocks(self.id)

        # leave all chats we are part of (auto-deletes direct & solo chats)
        self.chat_manager.leave_all_chats(self.id)

        # remove our trending item, if it's there
        self.trending_delete()

        # delete current and old profile photos
        self.clear_photo_s3_objects()

        # delete our own profile. Leave our stale item around so we can serialize
        self.dynamo.delete_user(self.id)

        if skip_cognito:
            # release our preferred_username from cognito
            try:
                self.cognito_client.clear_user_attribute(self.id, 'preferred_username')
            except self.cognito_client.user_pool_client.exceptions.UserNotFoundException:
                logger.warning(f'No cognito user pool entry found when deleting user `{self.id}`')
        else:
            self.cognito_client.delete_user_pool_entry(self.id)
            self.cognito_client.delete_identity_pool_entry(self.id)

        return self

    def set_accepted_eula_version(self, version):
        if version == self.item.get('acceptedEULAVersion'):
            return self
        self.item = self.dynamo.set_user_accepted_eula_version(self.id, version)
        return self

    def get_apns_token(self):
        endpoint_item = self.pinpoint_client.get_user_endpoints(self.id, 'APNS')
        return list(endpoint_item.values()).pop()['Address'] if endpoint_item else None

    def set_apns_token(self, token):
        if token is None:
            self.pinpoint_client.delete_user_endpoint(self.id, 'APNS')
        else:
            self.pinpoint_client.update_user_endpoint(self.id, 'APNS', token)
        return self

    def set_privacy_status(self, privacy_status):
        old_privacy_status = self.item.get('privacyStatus')
        if privacy_status == old_privacy_status:
            return self

        # are we changing from private to public?
        if old_privacy_status == UserPrivacyStatus.PRIVATE and privacy_status == UserPrivacyStatus.PUBLIC:
            self.follower_manager.accept_all_requested_follow_requests(self.id)
            self.follower_manager.delete_all_denied_follow_requests(self.id)

        self.item = self.dynamo.set_user_privacy_status(self.id, privacy_status)
        return self

    def update_username(self, username):
        old_username = self.item['username']
        if old_username == username:
            # no change was requested
            return self

        # validate and claim the lowercased username in cognito
        self.validate.username(username)
        try:
            self.cognito_client.set_user_attributes(self.id, {'preferred_username': username.lower()})
        except self.cognito_client.user_pool_client.exceptions.AliasExistsException:
            raise UserValidationException(f'Username `{username}` already taken (case-insensitive cmp)')

        self.item = self.dynamo.update_user_username(self.id, username, old_username)
        return self

    def update_photo(self, post_id):
        "Update photo. Set post_id=None to go back to the default profile pics"

        old_post_id = self.item.get('photoPostId')
        if post_id == old_post_id:
            return self

        if post_id:
            post = self.post_manager.get_post(post_id)
            if not post:
                raise UserException(f'Post `{post_id}` not found')
            if post.type != PostType.IMAGE:
                raise UserException(f'Post `{post_id}` does not have type `{PostType.IMAGE}`')
            if post.status != PostStatus.COMPLETED:
                raise UserException(f'Post `{post_id}` does not have status `{PostStatus.COMPLETED}`')
            if post.user_id != self.id:
                raise UserException(f'Post `{post_id}` does not belong to this user')
            if post.item.get('isVerified') is not True:
                raise UserException(f'Post `{post_id}` is not verified')

            # add the new s3 objects
            self.add_photo_s3_objects(post)

        # then dynamo
        self.item = self.dynamo.set_user_photo_post_id(self.id, post_id)

        # Leave the old images around as their may be existing urls out there that point to them
        # Could schedule a job to delete them a hour from now
        return self

    def add_photo_s3_objects(self, post):
        assert post.type == PostType.IMAGE
        for size in image_size.JPEGS:
            source_path = post.get_s3_image_path(size)
            dest_path = self.get_photo_path(size, photo_post_id=post.id)
            self.s3_uploads_client.copy_object(source_path, dest_path)

    def update_details(
        self,
        full_name=None,
        bio=None,
        gender=None,
        birthDate=None,
        language_code=None,
        theme_code=None,
        follow_counts_hidden=None,
        view_counts_hidden=None,
        comments_disabled=None,
        likes_disabled=None,
        sharing_disabled=None,
        verification_hidden=None,
    ):
        "To delete details, set them to the empty string. Ex: `full_name=''`"
        kwargs = {k: v for k, v in locals().items() if k != 'self' and v is not None}
        # remove writes where requested value matches pre-existing value
        kwargs = {k: v for k, v in kwargs.items() if v != self.item.get(stringcase.camelcase(k), '')}
        if kwargs:
            self.item = self.dynamo.set_user_details(self.id, **kwargs)
        return self

    def clear_photo_s3_objects(self):
        photo_dir_prefix = '/'.join([self.id, 'profile-photo', ''])
        self.s3_uploads_client.delete_objects_with_prefix(photo_dir_prefix)

    def start_change_contact_attribute(self, attribute_name, attribute_value):
        assert attribute_name in CONTACT_ATTRIBUTE_NAMES
        names = CONTACT_ATTRIBUTE_NAMES[attribute_name]

        # verify we actually need to do anything
        old_value = self.item.get(names['dynamo'])
        if old_value == attribute_value:
            raise UserVerificationException(f'User {attribute_name} already set to `{attribute_value}`')

        # first we set the users email to the new, unverified one, while also setting it to another property
        # this sends the verification email to the user
        attrs = {
            names['cognito']: attribute_value,
            f'custom:unverified_{names["short"]}': attribute_value,
        }
        self.cognito_client.set_user_attributes(self.id, attrs)

        # then if we have a verified version for the user stored in dynamo, set their main property in
        # cognito *back* to their verified version. This allows them to still use it to login.
        if old_value:
            attrs = {
                names['cognito']: old_value,
                f'{names["cognito"]}_verified': 'true',
            }
            self.cognito_client.set_user_attributes(self.id, attrs)
        return self

    def finish_change_contact_attribute(self, attribute_name, access_token, verification_code):
        assert attribute_name in CONTACT_ATTRIBUTE_NAMES
        names = CONTACT_ATTRIBUTE_NAMES[attribute_name]

        # first, figure out what that the value we're validating is
        user_attrs = self.cognito_client.get_user_attributes(self.id)
        value = user_attrs.get(f'custom:unverified_{names["short"]}')
        if not value:
            raise UserVerificationException(f'No unverified email found to validate for user `{self.id}`')

        # try to do the validation
        try:
            self.cognito_client.verify_user_attribute(access_token, names['cognito'], verification_code)
        except self.cognito_client.user_pool_client.exceptions.CodeMismatchException:
            raise UserVerificationException('Verification code is invalid')

        # success, update cognito, dynamo, then delete the temporary attribute in cognito
        attrs = {
            names['cognito']: value,
            f'{names["cognito"]}_verified': 'true',
        }
        self.cognito_client.set_user_attributes(self.id, attrs)
        self.item = self.dynamo.set_user_details(self.id, **{names['short']: value})
        self.cognito_client.clear_user_attribute(self.id, f'custom:unverified_{names["short"]}')
        return self

    def grant_subscription_bonus(self, now=None):
        now = now or pendulum.now('utc')
        expires_at = now + self.subscription_bonus_duration
        self.item = self.dynamo.grant_subscription(self.id, UserSubscriptionLevel.DIAMOND, now, expires_at)
        return self
