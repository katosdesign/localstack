from localstack.aws.api.s3 import BucketCannedACL, ObjectCannedACL, Permission, StorageClass

S3_VIRTUAL_HOST_FORWARDED_HEADER = "x-s3-vhost-forwarded-for"
S3_CHUNK_SIZE = 65536
S3_UPLOAD_PART_MIN_SIZE = 5242880

VALID_CANNED_ACLS_BUCKET = {
    # https://docs.aws.amazon.com/AmazonS3/latest/userguide/acl-overview.html#canned-acl
    # bucket-owner-read + bucket-owner-full-control are allowed, but ignored for buckets
    ObjectCannedACL.private,
    ObjectCannedACL.authenticated_read,
    ObjectCannedACL.aws_exec_read,
    ObjectCannedACL.bucket_owner_full_control,
    ObjectCannedACL.bucket_owner_read,
    ObjectCannedACL.public_read,
    ObjectCannedACL.public_read_write,
    BucketCannedACL.log_delivery_write,
}

VALID_ACL_PREDEFINED_GROUPS = {
    "http://acs.amazonaws.com/groups/global/AuthenticatedUsers",
    "http://acs.amazonaws.com/groups/global/AllUsers",
    "http://acs.amazonaws.com/groups/s3/LogDelivery",
}

VALID_GRANTEE_PERMISSIONS = {
    Permission.FULL_CONTROL,
    Permission.READ,
    Permission.READ_ACP,
    Permission.WRITE,
    Permission.WRITE_ACP,
}

VALID_STORAGE_CLASSES = [
    StorageClass.STANDARD,
    StorageClass.STANDARD_IA,
    StorageClass.GLACIER,
    StorageClass.GLACIER_IR,
    StorageClass.REDUCED_REDUNDANCY,
    StorageClass.ONEZONE_IA,
    StorageClass.INTELLIGENT_TIERING,
    StorageClass.DEEP_ARCHIVE,
]

# TODO validate those?
ARCHIVES_STORAGE_CLASSES = [
    StorageClass.GLACIER,
    StorageClass.GLACIER_IR,
    StorageClass.DEEP_ARCHIVE,
]

# response header overrides the client may request
ALLOWED_HEADER_OVERRIDES = {
    "ResponseContentType": "ContentType",
    "ResponseContentLanguage": "ContentLanguage",
    "ResponseExpires": "Expires",
    "ResponseCacheControl": "CacheControl",
    "ResponseContentDisposition": "ContentDisposition",
    "ResponseContentEncoding": "ContentEncoding",
}

# Whether to enable S3 bucket policy enforcement in moto - currently disabled, as some recent CDK versions
# are creating bucket policies that enforce aws:SecureTransport, which makes the CDK deployment fail.
# TODO: potentially look into making configurable
ENABLE_MOTO_BUCKET_POLICY_ENFORCEMENT = False


METADATA_SETTABLE_HEADERS = {
    "content-md5",
    "content-language",
    "content-type",
    "content-encoding",
    "cache-control",
    "content-disposition",
    "x-robots-tag",
}

STREAM_CHUNK_SIZE = 2048 * 2
