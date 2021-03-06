import io
import logging
import os

import boto3
import PIL
import PIL.Image
import PIL.ImageOps

logger = logging.getLogger()

DYNAMO_TABLE = os.environ.get('DYNAMO_TABLE')
S3_UPLOADS_BUCKET = os.environ.get('S3_UPLOADS_BUCKET')


class _ImageSize:

    file_ext = 'jpg'

    def __init__(self, name, max_dimensions):
        self.name = name
        self.max_dimensions = max_dimensions
        self.filename = f'{self.name}.{self.file_ext}'


NATIVE = _ImageSize('native', None)
K4 = _ImageSize('4K', (3840, 2160))
P1080 = _ImageSize('1080p', (1920, 1080))
P480 = _ImageSize('480p', (854, 480))
P64 = _ImageSize('64p', (114, 64))

# order matters, largest to smallest
THUMBNAILS = (K4, P1080, P480, P64)


class Migration:
    """
    Regenerate all thumbnails with new jpeg settings.
    """

    from_version = 5
    to_version = 6
    content_type = 'image/jpeg'

    def __init__(self, dynamo_table, s3_bucket):
        self.dynamo_table = dynamo_table
        self.s3_bucket = s3_bucket

    def run(self):
        for user in self.generate_all_users_to_migrate():
            self.migrate_user(user)

    def generate_all_users_to_migrate(self):
        "Return a generator of all items in the table that pass the filter"
        scan_kwargs = {
            'FilterExpression': 'begins_with(partitionKey, :pk_prefix) and schemaVersion = :fsv',
            'ExpressionAttributeValues': {':pk_prefix': 'user/', ':fsv': self.from_version},
        }
        while True:
            paginated = self.dynamo_table.scan(**scan_kwargs)
            for item in paginated['Items']:
                yield item
            if 'LastEvaluatedKey' not in paginated:
                break
            scan_kwargs['ExclusiveStartKey'] = paginated['LastEvaluatedKey']

    def migrate_user(self, user):
        user_id = user['userId']
        logger.warning(f'User `{user_id}`: starting migration')
        native_path = self.s3_get_image_path(user, NATIVE)
        native_data = self.s3_get_object_data(native_path) if native_path else None
        if native_data:
            try:
                im = PIL.Image.open(io.BytesIO(native_data))
            except PIL.UnidentifiedImageError:
                logger.warning(f'User `{user_id}`: s3: native image appears corrupted')
            else:
                im = PIL.ImageOps.exif_transpose(im)
                for size in THUMBNAILS:
                    im.thumbnail(size.max_dimensions)
                    data = io.BytesIO()
                    im.save(data, format='JPEG', quality=100, icc_profile=im.info.get('icc_profile'))
                    path = self.s3_get_image_path(user, size)
                    self.s3_put_object(user_id, path, data)
        self.dynamo_update_user_schema_version(user_id)

    def dynamo_update_user_schema_version(self, user_id):
        logger.warning(f'User `{user_id}`: dynamo: updating schema version')
        kwargs = {
            'Key': {'partitionKey': f'user/{user_id}', 'sortKey': 'profile'},
            'UpdateExpression': 'SET schemaVersion = :tsv',
            'ConditionExpression': 'attribute_exists(partitionKey) and schemaVersion = :fsv',
            'ExpressionAttributeValues': {':fsv': self.from_version, ':tsv': self.to_version},
        }
        logger.info(f'Applying update_item with kwargs: {kwargs}')
        self.dynamo_table.update_item(**kwargs)

    def s3_get_image_path(self, user, size):
        if photo_id := user.get('photoPostId'):
            return '/'.join([user['userId'], 'profile-photo', photo_id, size.filename])
        return None

    def s3_get_object_data(self, path):
        try:
            return self.s3_bucket.Object(path).get()['Body'].read()
        except self.s3_bucket.meta.client.exceptions.NoSuchKey:
            return None

    def s3_put_object(self, user_id, path, data):
        logger.warning(f'User `{user_id}`: s3: putting object at `{path}`')
        data.seek(0)
        self.s3_bucket.put_object(Key=path, Body=data, ContentType=self.content_type)


if __name__ == '__main__':
    assert DYNAMO_TABLE, 'Must set env variable DYNAMO_TABLE to dynamo table name'
    assert S3_UPLOADS_BUCKET, 'Must set env variable S3_UPLOADS_BUCKET to bucket name'

    dynamo_table = boto3.resource('dynamodb').Table(DYNAMO_TABLE)
    s3_bucket = boto3.resource('s3').Bucket(S3_UPLOADS_BUCKET)

    migration = Migration(dynamo_table, s3_bucket)
    migration.run()
