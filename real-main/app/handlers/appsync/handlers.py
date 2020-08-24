import logging
import os

import pendulum

from app import clients, models
from app.mixins.flag.enums import FlagStatus
from app.mixins.flag.exceptions import FlagException
from app.models.album.exceptions import AlbumException
from app.models.appstore.exceptions import AppStoreException
from app.models.block.enums import BlockStatus
from app.models.block.exceptions import BlockException
from app.models.card.exceptions import CardException
from app.models.chat.exceptions import ChatException
from app.models.chat_message.enums import ChatMessageNotificationType
from app.models.chat_message.exceptions import ChatMessageException
from app.models.comment.exceptions import CommentException
from app.models.follower.enums import FollowStatus
from app.models.follower.exceptions import FollowerException
from app.models.like.enums import LikeStatus
from app.models.like.exceptions import LikeException
from app.models.post.enums import PostStatus, PostType
from app.models.post.exceptions import PostException
from app.models.user.enums import UserStatus
from app.models.user.exceptions import UserException
from app.utils import image_size

from .. import xray
from . import routes
from .exceptions import ClientException

S3_UPLOADS_BUCKET = os.environ.get('S3_UPLOADS_BUCKET')
S3_PLACEHOLDER_PHOTOS_BUCKET = os.environ.get('S3_PLACEHOLDER_PHOTOS_BUCKET')

logger = logging.getLogger()
xray.patch_all()

secrets_manager_client = clients.SecretsManagerClient()
clients = {
    'apple': clients.AppleClient(),
    'appstore': clients.AppStoreClient(),
    'appsync': clients.AppSyncClient(),
    'cloudfront': clients.CloudFrontClient(secrets_manager_client.get_cloudfront_key_pair),
    'cognito': clients.CognitoClient(),
    'dynamo': clients.DynamoClient(),
    'facebook': clients.FacebookClient(),
    'pinpoint': clients.PinpointClient(),
    's3_uploads': clients.S3Client(S3_UPLOADS_BUCKET),
    's3_placeholder_photos': clients.S3Client(S3_PLACEHOLDER_PHOTOS_BUCKET),
}

# shared hash table of all managers, enables inter-manager communication
managers = {}
appstore_manager = managers.get('appstore') or models.AppStoreManager(clients, managers=managers)
album_manager = managers.get('album') or models.AlbumManager(clients, managers=managers)
block_manager = managers.get('block') or models.BlockManager(clients, managers=managers)
card_manager = managers.get('card') or models.CardManager(clients, managers=managers)
chat_manager = managers.get('chat') or models.ChatManager(clients, managers=managers)
chat_message_manager = managers.get('chat_message') or models.ChatMessageManager(clients, managers=managers)
comment_manager = managers.get('comment') or models.CommentManager(clients, managers=managers)
follower_manager = managers.get('follower') or models.FollowerManager(clients, managers=managers)
like_manager = managers.get('like') or models.LikeManager(clients, managers=managers)
post_manager = managers.get('post') or models.PostManager(clients, managers=managers)
user_manager = managers.get('user') or models.UserManager(clients, managers=managers)


def validate_caller(func):
    "Decorator that inits a caller_user model and verifies the caller is ACTIVE"

    def wrapper(caller_user_id, arguments, source, context):
        caller_user = user_manager.get_user(caller_user_id)
        if not caller_user:
            raise ClientException(f'User `{caller_user_id}` does not exist')
        if caller_user.status != UserStatus.ACTIVE:
            raise ClientException(f'User `{caller_user_id}` is not ACTIVE')
        return func(caller_user, arguments, source, context)

    return wrapper


@routes.register('Mutation.createCognitoOnlyUser')
def create_cognito_only_user(caller_user_id, arguments, source, context):
    username = arguments['username']
    full_name = arguments.get('fullName')
    try:
        user = user_manager.create_cognito_only_user(caller_user_id, username, full_name=full_name)
    except UserException as err:
        raise ClientException(str(err))
    return user.serialize(caller_user_id)


@routes.register('Mutation.createAppleUser')
def create_apple_user(caller_user_id, arguments, source, context):
    username = arguments['username']
    full_name = arguments.get('fullName')
    apple_token = arguments['appleIdToken']
    try:
        user = user_manager.create_federated_user(
            'apple', caller_user_id, username, apple_token, full_name=full_name
        )
    except UserException as err:
        raise ClientException(str(err))
    return user.serialize(caller_user_id)


@routes.register('Mutation.createFacebookUser')
def create_facebook_user(caller_user_id, arguments, source, context):
    username = arguments['username']
    full_name = arguments.get('fullName')
    facebook_token = arguments['facebookAccessToken']
    try:
        user = user_manager.create_federated_user(
            'facebook', caller_user_id, username, facebook_token, full_name=full_name
        )
    except UserException as err:
        raise ClientException(str(err))
    return user.serialize(caller_user_id)


@routes.register('Mutation.startChangeUserEmail')
@validate_caller
def start_change_user_email(caller_user, arguments, source, context):
    email = arguments['email']
    try:
        caller_user.start_change_contact_attribute('email', email)
    except UserException as err:
        raise ClientException(str(err))
    return caller_user.serialize(caller_user.id)


@routes.register('Mutation.finishChangeUserEmail')
@validate_caller
def finish_change_user_email(caller_user, arguments, source, context):
    access_token = arguments['cognitoAccessToken']
    code = arguments['verificationCode']
    try:
        caller_user.finish_change_contact_attribute('email', access_token, code)
    except UserException as err:
        raise ClientException(str(err))
    return caller_user.serialize(caller_user.id)


@routes.register('Mutation.startChangeUserPhoneNumber')
@validate_caller
def start_change_user_phone_number(caller_user, arguments, source, context):
    phone = arguments['phoneNumber']
    try:
        caller_user.start_change_contact_attribute('phone', phone)
    except UserException as err:
        raise ClientException(str(err))
    return caller_user.serialize(caller_user.id)


@routes.register('Mutation.finishChangeUserPhoneNumber')
@validate_caller
def finish_change_user_phone_number(caller_user, arguments, source, context):
    access_token = arguments['cognitoAccessToken']
    code = arguments['verificationCode']
    try:
        caller_user.finish_change_contact_attribute('phone', access_token, code)
    except UserException as err:
        raise ClientException(str(err))
    return caller_user.serialize(caller_user.id)


@routes.register('Mutation.setUserDetails')
@validate_caller
def set_user_details(caller_user, arguments, source, context):
    username = arguments.get('username')
    full_name = arguments.get('fullName')
    bio = arguments.get('bio')
    gender = arguments.get('gender')
    birthDate = arguments.get('birthDate')
    photo_post_id = arguments.get('photoPostId')
    privacy_status = arguments.get('privacyStatus')
    follow_counts_hidden = arguments.get('followCountsHidden')
    view_counts_hidden = arguments.get('viewCountsHidden')
    language_code = arguments.get('languageCode')
    theme_code = arguments.get('themeCode')
    comments_disabled = arguments.get('commentsDisabled')
    likes_disabled = arguments.get('likesDisabled')
    sharing_disabled = arguments.get('sharingDisabled')
    verification_hidden = arguments.get('verificationHidden')

    args = (
        username,
        full_name,
        bio,
        gender,
        birthDate,
        photo_post_id,
        privacy_status,
        follow_counts_hidden,
        language_code,
        theme_code,
        comments_disabled,
        likes_disabled,
        sharing_disabled,
        verification_hidden,
        view_counts_hidden,
    )
    if all(v is None for v in args):
        raise ClientException('Called without any arguments... probably not what you intended?')

    # are we claiming a new username?
    if username is not None:
        try:
            caller_user.update_username(username)
        except UserException as err:
            raise ClientException(str(err))

    # are we setting a new profile picture?
    if photo_post_id is not None:
        post_id = photo_post_id if photo_post_id != '' else None
        try:
            caller_user.update_photo(post_id)
        except UserException as err:
            raise ClientException(str(err))

    # are we changing our privacy status?
    if privacy_status is not None:
        caller_user.set_privacy_status(privacy_status)

    # update the simple properties
    caller_user.update_details(
        full_name=full_name,
        bio=bio,
        gender=gender,
        birthDate=birthDate,
        language_code=language_code,
        theme_code=theme_code,
        follow_counts_hidden=follow_counts_hidden,
        view_counts_hidden=view_counts_hidden,
        comments_disabled=comments_disabled,
        likes_disabled=likes_disabled,
        sharing_disabled=sharing_disabled,
        verification_hidden=verification_hidden,
    )
    return caller_user.serialize(caller_user.id)


@routes.register('Mutation.setUserAcceptedEULAVersion')
@validate_caller
def set_user_accepted_eula_version(caller_user, arguments, source, context):
    version = arguments['version']

    # use the empty string to request deleting
    if version == '':
        version = None

    caller_user.set_accepted_eula_version(version)
    return caller_user.serialize(caller_user.id)


@routes.register('Mutation.setUserAPNSToken')
@validate_caller
def set_user_apns_token(caller_user, arguments, source, context):
    token = arguments['token']

    # use the empty string to request deleting
    if token == '':
        token = None

    caller_user.set_apns_token(token)
    return caller_user.serialize(caller_user.id)


@routes.register('Mutation.resetUser')
def reset_user(caller_user_id, arguments, source, context):
    new_username = arguments.get('newUsername') or None  # treat empty string like null

    # resetUser may be called when user exists in cognito but not in dynamo
    user = user_manager.get_user(caller_user_id)
    if user:
        user.delete(skip_cognito=True)

    if new_username:
        # equivalent to calling Mutation.createCognitoOnlyUser()
        try:
            user = user_manager.create_cognito_only_user(caller_user_id, new_username)
        except UserException as err:
            raise ClientException(str(err))

    return user.serialize(caller_user_id) if user else None


@routes.register('Mutation.disableUser')
def disable_user(caller_user_id, arguments, source, context):
    # mark our user as in the process of deleting
    user = user_manager.get_user(caller_user_id)
    if not user:
        raise ClientException(f'User `{caller_user_id}` does not exist')

    user.disable()
    return user.serialize(caller_user_id)


@routes.register('Mutation.deleteUser')
def delete_user(caller_user_id, arguments, source, context):
    user = user_manager.get_user(caller_user_id)
    if not user:
        raise ClientException(f'User `{caller_user_id}` does not exist')

    user.delete()
    return user.serialize(caller_user_id)


@routes.register('Mutation.grantUserSubscriptionBonus')
@validate_caller
def grant_user_subscription_bonus(caller_user, arguments, source, context):
    try:
        caller_user.grant_subscription_bonus()
    except UserException as err:
        raise ClientException(str(err))
    return caller_user.serialize(caller_user.id)


@routes.register('Mutation.addAppStoreReceipt')
@validate_caller
def add_app_store_receipt(caller_user, arguments, source, context):
    receipt_data = arguments['receiptData']
    try:
        appstore_manager.add_receipt(receipt_data, caller_user.id)
    except AppStoreException as err:
        raise ClientException(str(err))
    return True


@routes.register('User.photo')
def user_photo(caller_user_id, arguments, source, context):
    user = user_manager.init_user(source)
    native_url = user.get_photo_url(image_size.NATIVE)
    if not native_url:
        return None
    return {
        'url': native_url,
        'url64p': user.get_photo_url(image_size.P64),
        'url480p': user.get_photo_url(image_size.P480),
        'url1080p': user.get_photo_url(image_size.P1080),
        'url4k': user.get_photo_url(image_size.K4),
    }


@routes.register('Mutation.followUser')
@validate_caller
def follow_user(caller_user, arguments, source, context):
    follower_user = caller_user
    followed_user_id = arguments['userId']

    if follower_user.id == followed_user_id:
        raise ClientException('User cannot follow themselves')

    followed_user = user_manager.get_user(followed_user_id)
    if not followed_user:
        raise ClientException(f'No user profile found for followed `{followed_user_id}`')

    try:
        follow = follower_manager.request_to_follow(follower_user, followed_user)
    except FollowerException as err:
        raise ClientException(str(err))

    resp = followed_user.serialize(caller_user.id)
    resp['followedStatus'] = follow.status
    if follow.status == FollowStatus.FOLLOWING:
        resp['followerCount'] = followed_user.item.get('followerCount', 0) + 1
    return resp


@routes.register('Mutation.unfollowUser')
@validate_caller
def unfollow_user(caller_user, arguments, source, context):
    follower_user = caller_user
    followed_user_id = arguments['userId']

    follow = follower_manager.get_follow(follower_user.id, followed_user_id)
    if not follow:
        raise ClientException(f'User `{follower_user.id}` is not following `{followed_user_id}`')

    try:
        follow.unfollow()
    except FollowerException as err:
        raise ClientException(str(err))

    resp = user_manager.get_user(followed_user_id, strongly_consistent=True).serialize(caller_user.id)
    resp['followedStatus'] = follow.status
    return resp


@routes.register('Mutation.acceptFollowerUser')
@validate_caller
def accept_follower_user(caller_user, arguments, source, context):
    followed_user = caller_user
    follower_user_id = arguments['userId']

    follow = follower_manager.get_follow(follower_user_id, followed_user.id)
    if not follow:
        raise ClientException(f'User `{follower_user_id}` has not requested to follow user `{followed_user.id}`')

    try:
        follow.accept()
    except FollowerException as err:
        raise ClientException(str(err))

    resp = user_manager.get_user(follower_user_id, strongly_consistent=True).serialize(caller_user.id)
    resp['followerStatus'] = follow.status
    return resp


@routes.register('Mutation.denyFollowerUser')
@validate_caller
def deny_follower_user(caller_user, arguments, source, context):
    followed_user = caller_user
    follower_user_id = arguments['userId']

    follow = follower_manager.get_follow(follower_user_id, followed_user.id)
    if not follow:
        raise ClientException(f'User `{follower_user_id}` has not requested to follow user `{followed_user.id}`')

    try:
        follow.deny()
    except FollowerException as err:
        raise ClientException(str(err))

    resp = user_manager.get_user(follower_user_id, strongly_consistent=True).serialize(caller_user.id)
    resp['followerStatus'] = follow.status
    return resp


@routes.register('Mutation.blockUser')
@validate_caller
def block_user(caller_user, arguments, source, context):
    blocker_user = caller_user
    blocked_user_id = arguments['userId']

    if blocker_user.id == blocked_user_id:
        raise ClientException('Cannot block yourself')

    blocked_user = user_manager.get_user(blocked_user_id)
    if not blocked_user:
        raise ClientException(f'User `{blocked_user_id}` does not exist')

    try:
        block_manager.block(blocker_user, blocked_user)
    except BlockException as err:
        raise ClientException(str(err))

    resp = blocked_user.serialize(caller_user.id)
    resp['blockedStatus'] = BlockStatus.BLOCKING
    return resp


@routes.register('Mutation.unblockUser')
@validate_caller
def unblock_user(caller_user, arguments, source, context):
    blocker_user = caller_user
    blocked_user_id = arguments['userId']

    if blocker_user.id == blocked_user_id:
        raise ClientException('Cannot unblock yourself')

    blocked_user = user_manager.get_user(blocked_user_id)
    if not blocked_user:
        raise ClientException(f'User `{blocked_user_id}` does not exist')

    try:
        block_manager.unblock(blocker_user, blocked_user)
    except BlockException as err:
        raise ClientException(str(err))

    resp = blocked_user.serialize(caller_user.id)
    resp['blockedStatus'] = BlockStatus.NOT_BLOCKING
    return resp


@routes.register('Mutation.addPost')
@validate_caller
def add_post(caller_user, arguments, source, context):
    post_id = arguments['postId']
    post_type = arguments.get('postType') or PostType.IMAGE
    text = arguments.get('text')
    image_input = arguments.get('imageInput')
    album_id = arguments.get('albumId')
    set_as_user_photo = arguments.get('setAsUserPhoto')
    comments_disabled = arguments.get('commentsDisabled')
    likes_disabled = arguments.get('likesDisabled')
    sharing_disabled = arguments.get('sharingDisabled')
    verification_hidden = arguments.get('verificationHidden')

    lifetime_iso = arguments.get('lifetime')
    if lifetime_iso:
        try:
            lifetime_duration = pendulum.parse(lifetime_iso)
        except pendulum.exceptions.ParserError:
            raise ClientException(f'Unable to parse lifetime `{lifetime_iso}`')
        if not isinstance(lifetime_duration, pendulum.Duration):
            raise ClientException(f'Unable to parse lifetime `{lifetime_iso}` as duration')
    else:
        lifetime_duration = None

    try:
        post = post_manager.add_post(
            caller_user,
            post_id,
            post_type,
            image_input=image_input,
            text=text,
            lifetime_duration=lifetime_duration,
            album_id=album_id,
            comments_disabled=comments_disabled,
            likes_disabled=likes_disabled,
            sharing_disabled=sharing_disabled,
            verification_hidden=verification_hidden,
            set_as_user_photo=set_as_user_photo,
        )
    except PostException as err:
        raise ClientException(str(err))

    return post.serialize(caller_user.id)


@routes.register('Post.image')
def post_image(caller_user_id, arguments, source, context):
    post = post_manager.get_post(source['postId'])

    if not post or post.status == PostStatus.DELETING:
        return None

    if post.type == PostType.TEXT_ONLY:
        return None

    if post.status not in (PostStatus.COMPLETED, PostStatus.ARCHIVED):
        return None

    image_item = post.image_item.copy() if post.image_item else {}
    image_item.update(
        {
            'url': post.get_image_readonly_url(image_size.NATIVE),
            'url64p': post.get_image_readonly_url(image_size.P64),
            'url480p': post.get_image_readonly_url(image_size.P480),
            'url1080p': post.get_image_readonly_url(image_size.P1080),
            'url4k': post.get_image_readonly_url(image_size.K4),
        }
    )
    return image_item


@routes.register('Post.imageUploadUrl')
def post_image_upload_url(caller_user_id, arguments, source, context):
    post_id = source['postId']
    user_id = source['postedByUserId']

    if caller_user_id != user_id:
        return None

    post = post_manager.get_post(post_id)
    if not post or post.type != PostType.IMAGE or post.status != PostStatus.PENDING:
        return None

    return post.get_image_writeonly_url()


@routes.register('Post.video')
def post_video(caller_user_id, arguments, source, context):
    post = post_manager.get_post(source['postId'])

    statuses = (PostStatus.COMPLETED, PostStatus.ARCHIVED)
    if not post or post.type != PostType.VIDEO or post.status not in statuses:
        return None

    return {
        'urlMasterM3U8': post.get_hls_master_m3u8_url(),
        'accessCookies': post.get_hls_access_cookies(),
    }


@routes.register('Post.videoUploadUrl')
def post_video_upload_url(caller_user_id, arguments, source, context):
    post_id = source['postId']
    user_id = source['postedByUserId']

    if caller_user_id != user_id:
        return None

    post = post_manager.get_post(post_id)
    if not post or post.type != PostType.VIDEO or post.status != PostStatus.PENDING:
        return None

    return post.get_video_writeonly_url()


@routes.register('Mutation.editPost')
@validate_caller
def edit_post(caller_user, arguments, source, context):
    post_id = arguments['postId']
    edit_kwargs = {
        'text': arguments.get('text'),
        'comments_disabled': arguments.get('commentsDisabled'),
        'likes_disabled': arguments.get('likesDisabled'),
        'sharing_disabled': arguments.get('sharingDisabled'),
        'verification_hidden': arguments.get('verificationHidden'),
    }

    post = post_manager.get_post(post_id)
    if not post:
        raise ClientException(f'Post `{post_id}` does not exist')

    if caller_user.id != post.user_id:
        raise ClientException("Cannot edit another User's post")

    try:
        post.set(**edit_kwargs)
    except PostException as err:
        raise ClientException(str(err))

    return post.serialize(caller_user.id)


@routes.register('Mutation.editPostAlbum')
@validate_caller
def edit_post_album(caller_user, arguments, source, context):
    post_id = arguments['postId']
    album_id = arguments.get('albumId') or None

    post = post_manager.get_post(post_id)
    if not post:
        raise ClientException(f'Post `{post_id}` does not exist')

    if caller_user.id != post.user_id:
        raise ClientException("Cannot edit another user's post")

    try:
        post.set_album(album_id)
    except PostException as err:
        raise ClientException(str(err))

    return post.serialize(caller_user.id)


@routes.register('Mutation.editPostAlbumOrder')
@validate_caller
def edit_post_album_order(caller_user, arguments, source, context):
    post_id = arguments['postId']
    preceding_post_id = arguments.get('precedingPostId')

    post = post_manager.get_post(post_id)
    if not post:
        raise ClientException(f'Post `{post_id}` does not exist')

    if caller_user.id != post.user_id:
        raise ClientException("Cannot edit another user's post")

    try:
        post.set_album_order(preceding_post_id)
    except PostException as err:
        raise ClientException(str(err))

    return post.serialize(caller_user.id)


@routes.register('Mutation.editPostExpiresAt')
@validate_caller
def edit_post_expires_at(caller_user, arguments, source, context):
    post_id = arguments['postId']
    expires_at_str = arguments.get('expiresAt')
    expires_at = pendulum.parse(expires_at_str) if expires_at_str else None

    post = post_manager.get_post(post_id)
    if not post:
        raise ClientException(f'Post `{post_id}` does not exist')

    if caller_user.id != post.user_id:
        raise ClientException("Cannot edit another User's post")

    if expires_at and expires_at < pendulum.now('utc'):
        raise ClientException("Cannot set expiresAt to date time in the past: `{expires_at}`")

    post.set_expires_at(expires_at)
    return post.serialize(caller_user.id)


@routes.register('Mutation.flagPost')
@validate_caller
def flag_post(caller_user, arguments, source, context):
    post_id = arguments['postId']

    post = post_manager.get_post(post_id)
    if not post:
        raise ClientException(f'Post `{post_id}` does not exist')

    try:
        post.flag(caller_user)
    except (PostException, FlagException) as err:
        raise ClientException(str(err))

    resp = post.serialize(caller_user.id)
    resp['flagStatus'] = FlagStatus.FLAGGED
    return resp


@routes.register('Mutation.archivePost')
@validate_caller
def archive_post(caller_user, arguments, source, context):
    post_id = arguments['postId']

    post = post_manager.get_post(post_id)
    if not post:
        raise ClientException(f'Post `{post_id}` does not exist')

    if caller_user.id != post.user_id:
        raise ClientException("Cannot archive another User's post")

    try:
        post.archive()
    except PostException as err:
        raise ClientException(str(err))

    return post.serialize(caller_user.id)


@routes.register('Mutation.deletePost')
@validate_caller
def delete_post(caller_user, arguments, source, context):
    post_id = arguments['postId']

    post = post_manager.get_post(post_id)
    if not post:
        raise ClientException(f'Post `{post_id}` does not exist')

    if caller_user.id != post.user_id:
        raise ClientException("Cannot delete another User's post")

    try:
        post = post.delete()
    except PostException as err:
        raise ClientException(str(err))

    return post.serialize(caller_user.id)


@routes.register('Mutation.restoreArchivedPost')
@validate_caller
def restore_archived_post(caller_user, arguments, source, context):
    post_id = arguments['postId']

    post = post_manager.get_post(post_id)
    if not post:
        raise ClientException(f'Post `{post_id}` does not exist')

    if caller_user.id != post.user_id:
        raise ClientException("Cannot restore another User's post")

    try:
        post.restore()
    except PostException as err:
        raise ClientException(str(err))

    return post.serialize(caller_user.id)


@routes.register('Mutation.onymouslyLikePost')
@validate_caller
def onymously_like_post(caller_user, arguments, source, context):
    post_id = arguments['postId']

    post = post_manager.get_post(post_id)
    if not post:
        raise ClientException(f'Post `{post_id}` does not exist')

    try:
        like_manager.like_post(caller_user, post, LikeStatus.ONYMOUSLY_LIKED)
    except LikeException as err:
        raise ClientException(str(err))

    resp = post.serialize(caller_user.id)
    resp['likeStatus'] = LikeStatus.ONYMOUSLY_LIKED
    return resp


@routes.register('Mutation.anonymouslyLikePost')
@validate_caller
def anonymously_like_post(caller_user, arguments, source, context):
    post_id = arguments['postId']

    post = post_manager.get_post(post_id)
    if not post:
        raise ClientException(f'Post `{post_id}` does not exist')

    try:
        like_manager.like_post(caller_user, post, LikeStatus.ANONYMOUSLY_LIKED)
    except LikeException as err:
        raise ClientException(str(err))

    resp = post.serialize(caller_user.id)
    resp['likeStatus'] = LikeStatus.ANONYMOUSLY_LIKED
    return resp


@routes.register('Mutation.dislikePost')
@validate_caller
def dislike_post(caller_user, arguments, source, context):
    post_id = arguments['postId']

    post = post_manager.dynamo.get_post(post_id)
    if not post:
        raise ClientException(f'Post `{post_id}` does not exist')

    like = like_manager.get_like(caller_user.id, post_id)
    if not like:
        raise ClientException(f'User has not liked post `{post_id}`, thus cannot dislike it')

    prev_status = like.item['likeStatus']
    like.dislike()

    resp = post_manager.init_post(post).serialize(caller_user.id)
    post_like_count = 'onymousLikeCount' if prev_status == LikeStatus.ONYMOUSLY_LIKED else 'anonymousLikeCount'
    if resp.get(post_like_count, 0) > 0:
        resp[post_like_count] -= 1
    resp['likeStatus'] = LikeStatus.NOT_LIKED
    return resp


@routes.register('Mutation.reportPostViews')
@validate_caller
def report_post_views(caller_user, arguments, source, context):
    post_ids = arguments['postIds']
    if len(post_ids) == 0:
        raise ClientException('A minimum of 1 post id must be reported')
    if len(post_ids) > 100:
        raise ClientException('A max of 100 post ids may be reported at a time')

    post_manager.record_views(post_ids, caller_user.id)
    return True


@routes.register('Mutation.addComment')
@validate_caller
def add_comment(caller_user, arguments, source, context):
    comment_id = arguments['commentId']
    post_id = arguments['postId']
    text = arguments['text']

    try:
        comment = comment_manager.add_comment(comment_id, post_id, caller_user.id, text)
    except CommentException as err:
        raise ClientException(str(err))

    return comment.serialize(caller_user.id)


@routes.register('Mutation.deleteComment')
@validate_caller
def delete_comment(caller_user, arguments, source, context):
    comment_id = arguments['commentId']

    comment = comment_manager.get_comment(comment_id)
    if not comment:
        raise ClientException(f'No comment with id `{comment_id}` found')

    try:
        comment.delete(deleter_user_id=caller_user.id)
    except CommentException as err:
        raise ClientException(str(err))

    return comment.serialize(caller_user.id)


@routes.register('Mutation.flagComment')
@validate_caller
def flag_comment(caller_user, arguments, source, context):
    comment_id = arguments['commentId']

    comment = comment_manager.get_comment(comment_id)
    if not comment:
        raise ClientException(f'Comment `{comment_id}` does not exist')

    try:
        comment.flag(caller_user)
    except (CommentException, FlagException) as err:
        raise ClientException(str(err))

    resp = comment.serialize(caller_user.id)
    resp['flagStatus'] = FlagStatus.FLAGGED
    return resp


@routes.register('Mutation.deleteCard')
@validate_caller
def delete_card(caller_user, arguments, source, context):
    card_id = arguments['cardId']

    card = card_manager.get_card(card_id)
    if not card:
        raise ClientException(f'No card with id `{card_id}` found')

    if caller_user.id != card.user_id:
        raise ClientException(f'Caller `{caller_user.id}` does not own Card `{card_id}`')

    try:
        card.delete()
    except CardException as err:
        raise ClientException(str(err))

    return card.serialize(caller_user.id)


@routes.register('Card.thumbnail')
def card_thumbnail(caller_user_id, arguments, source, context):
    card = card_manager.get_card(source['cardId'])
    if card and card.post and card.post.type != PostType.TEXT_ONLY:
        return {
            'url': card.post.get_image_readonly_url(image_size.NATIVE),
            'url64p': card.post.get_image_readonly_url(image_size.P64),
            'url480p': card.post.get_image_readonly_url(image_size.P480),
            'url1080p': card.post.get_image_readonly_url(image_size.P1080),
            'url4k': card.post.get_image_readonly_url(image_size.K4),
        }
    return None


@routes.register('Mutation.addAlbum')
@validate_caller
def add_album(caller_user, arguments, source, context):
    album_id = arguments['albumId']
    name = arguments['name']
    description = arguments.get('description')

    try:
        album = album_manager.add_album(caller_user.id, album_id, name, description=description)
    except AlbumException as err:
        raise ClientException(str(err))

    return album.serialize(caller_user.id)


@routes.register('Mutation.editAlbum')
@validate_caller
def edit_album(caller_user, arguments, source, context):
    album_id = arguments['albumId']
    name = arguments.get('name')
    description = arguments.get('description')

    if name is None and description is None:
        raise ClientException('Called without any arguments... probably not what you intended?')

    album = album_manager.get_album(album_id)
    if not album:
        raise ClientException(f'Album `{album_id}` does not exist')

    if album.user_id != caller_user.id:
        raise ClientException(f'Caller `{caller_user.id}` does not own Album `{album_id}`')

    try:
        album.update(name=name, description=description)
    except AlbumException as err:
        raise ClientException(str(err))

    return album.serialize(caller_user.id)


@routes.register('Mutation.deleteAlbum')
@validate_caller
def delete_album(caller_user, arguments, source, context):
    album_id = arguments['albumId']

    album = album_manager.get_album(album_id)
    if not album:
        raise ClientException(f'Album `{album_id}` does not exist')

    if album.user_id != caller_user.id:
        raise ClientException(f'Caller `{caller_user.id}` does not own Album `{album_id}`')

    try:
        album.delete()
    except AlbumException as err:
        raise ClientException(str(err))

    return album.serialize(caller_user.id)


@routes.register('Album.art')
def album_art(caller_user_id, arguments, source, context):
    album = album_manager.init_album(source)
    return {
        'url': album.get_art_image_url(image_size.NATIVE),
        'url64p': album.get_art_image_url(image_size.P64),
        'url480p': album.get_art_image_url(image_size.P480),
        'url1080p': album.get_art_image_url(image_size.P1080),
        'url4k': album.get_art_image_url(image_size.K4),
    }


@routes.register('Mutation.createDirectChat')
@validate_caller
def create_direct_chat(caller_user, arguments, source, context):
    chat_id, user_id = arguments['chatId'], arguments['userId']
    message_id, message_text = arguments['messageId'], arguments['messageText']

    user = user_manager.get_user(user_id)
    if not user:
        raise ClientException(f'User `{user_id}` does not exist')

    now = pendulum.now('utc')
    try:
        chat = chat_manager.add_direct_chat(chat_id, caller_user.id, user_id, now=now)
        msg = chat_message_manager.add_chat_message(message_id, message_text, chat_id, caller_user.id, now=now)
    except ChatException as err:
        raise ClientException(str(err))

    msg.trigger_notifications(ChatMessageNotificationType.ADDED, user_ids=[user_id])
    chat.refresh_item(strongly_consistent=True)
    return chat.item


@routes.register('Mutation.createGroupChat')
@validate_caller
def create_group_chat(caller_user, arguments, source, context):
    chat_id, user_ids, name = arguments['chatId'], arguments['userIds'], arguments.get('name')
    message_id, message_text = arguments['messageId'], arguments['messageText']

    try:
        chat = chat_manager.add_group_chat(chat_id, caller_user, name=name)
        chat.add(caller_user, user_ids)
        message = chat_message_manager.add_chat_message(message_id, message_text, chat_id, caller_user.id)
    except ChatException as err:
        raise ClientException(str(err))

    message.trigger_notifications(ChatMessageNotificationType.ADDED, user_ids=user_ids)
    chat.refresh_item(strongly_consistent=True)
    return chat.item


@routes.register('Mutation.editGroupChat')
@validate_caller
def edit_group_chat(caller_user, arguments, source, context):
    chat_id = arguments['chatId']
    name = arguments.get('name')

    chat = chat_manager.get_chat(chat_id)
    if not chat or not chat.is_member(caller_user.id):
        raise ClientException(f'User `{caller_user.id}` is not a member of chat `{chat_id}`')

    try:
        chat.edit(caller_user, name=name)
    except ChatException as err:
        raise ClientException(str(err))

    return chat.item


@routes.register('Mutation.addToGroupChat')
@validate_caller
def add_to_group_chat(caller_user, arguments, source, context):
    chat_id, user_ids = arguments['chatId'], arguments['userIds']

    chat = chat_manager.get_chat(chat_id)
    if not chat or not chat.is_member(caller_user.id):
        raise ClientException(f'User `{caller_user.id}` is not a member of chat `{chat_id}`')

    try:
        chat.add(caller_user, user_ids)
    except ChatException as err:
        raise ClientException(str(err))

    return chat.item


@routes.register('Mutation.leaveGroupChat')
@validate_caller
def leave_group_chat(caller_user, arguments, source, context):
    chat_id = arguments['chatId']

    chat = chat_manager.get_chat(chat_id)
    if not chat or not chat.is_member(caller_user.id):
        raise ClientException(f'User `{caller_user.id}` is not a member of chat `{chat_id}`')

    try:
        chat.leave(caller_user)
    except ChatException as err:
        raise ClientException(str(err))

    return chat.item


@routes.register('Mutation.reportChatViews')
@validate_caller
def report_chat_views(caller_user, arguments, source, context):
    chat_ids = arguments['chatIds']
    if len(chat_ids) == 0:
        raise ClientException('A minimum of 1 chat id must be reported')
    if len(chat_ids) > 100:
        raise ClientException('A max of 100 chat ids may be reported at a time')

    chat_manager.record_views(chat_ids, caller_user.id)
    return True


@routes.register('Mutation.flagChat')
@validate_caller
def flag_chat(caller_user, arguments, source, context):
    chat_id = arguments['chatId']

    chat = chat_manager.get_chat(chat_id)
    if not chat:
        raise ClientException(f'Chat `{chat_id}` does not exist')

    try:
        chat.flag(caller_user)
    except (ChatException, FlagException) as err:
        raise ClientException(str(err))

    resp = chat.item.copy()
    resp['flagStatus'] = FlagStatus.FLAGGED
    return resp


@routes.register('Mutation.addChatMessage')
@validate_caller
def add_chat_message(caller_user, arguments, source, context):
    chat_id, message_id, text = arguments['chatId'], arguments['messageId'], arguments['text']

    chat = chat_manager.get_chat(chat_id)
    if not chat or not chat.is_member(caller_user.id):
        raise ClientException(f'User `{caller_user.id}` is not a member of chat `{chat_id}`')

    try:
        message = chat_message_manager.add_chat_message(message_id, text, chat_id, caller_user.id)
    except ChatException as err:
        raise ClientException(str(err))

    message.trigger_notifications(ChatMessageNotificationType.ADDED)
    return message.serialize(caller_user.id)


@routes.register('Mutation.editChatMessage')
@validate_caller
def edit_chat_message(caller_user, arguments, source, context):
    message_id, text = arguments['messageId'], arguments['text']

    message = chat_message_manager.get_chat_message(message_id)
    if not message or message.user_id != caller_user.id:
        raise ClientException(f'User `{caller_user.id}` cannot edit message `{message_id}`')

    try:
        message.edit(text)
    except ChatException as err:
        raise ClientException(str(err))

    message.trigger_notifications(ChatMessageNotificationType.EDITED)
    return message.serialize(caller_user.id)


@routes.register('Mutation.deleteChatMessage')
@validate_caller
def delete_chat_message(caller_user, arguments, source, context):
    message_id = arguments['messageId']

    message = chat_message_manager.get_chat_message(message_id)
    if not message or message.user_id != caller_user.id:
        raise ClientException(f'User `{caller_user.id}` cannot delete message `{message_id}`')

    try:
        message.delete()
    except ChatException as err:
        raise ClientException(str(err))

    message.trigger_notifications(ChatMessageNotificationType.DELETED)
    return message.serialize(caller_user.id)


@routes.register('Mutation.flagChatMessage')
@validate_caller
def flag_chat_message(caller_user, arguments, source, context):
    message_id = arguments['messageId']

    message = chat_message_manager.get_chat_message(message_id)
    if not message:
        raise ClientException(f'ChatMessage `{message_id}` does not exist')

    try:
        message.flag(caller_user)
    except (ChatMessageException, FlagException) as err:
        raise ClientException(str(err))

    resp = message.serialize(caller_user.id)
    resp['flagStatus'] = FlagStatus.FLAGGED
    return resp


@routes.register('Mutation.lambdaClientError')
def lambda_client_error(caller_user_id, arguments, source, context):
    request_id = getattr(context, 'aws_request_id', None)
    raise ClientException(f'Test of lambda client error, request `{request_id}`')


@routes.register('Mutation.lambdaServerError')
def lambda_server_error(caller_user_id, arguments, source, context):
    request_id = getattr(context, 'aws_request_id', None)
    raise Exception(f'Test of lambda server error, request `{request_id}`')
