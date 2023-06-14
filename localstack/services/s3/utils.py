import datetime
import hashlib
import re
import zlib
from typing import Dict, Literal, Optional, Tuple, Type, Union

import moto.s3.models as moto_s3_models
from botocore.exceptions import ClientError
from botocore.utils import InvalidArnException
from moto.s3.exceptions import MissingBucket
from moto.s3.models import FakeBucket, FakeDeleteMarker, FakeKey
from werkzeug.datastructures.headers import Headers

from localstack import config
from localstack.aws.api import CommonServiceException
from localstack.aws.api.s3 import (
    BucketName,
    ChecksumAlgorithm,
    InvalidArgument,
    Metadata,
    MethodNotAllowed,
    NoSuchBucket,
    NoSuchKey,
    ObjectKey,
    Owner,
)
from localstack.services.s3.constants import (
    METADATA_SETTABLE_HEADERS,
    S3_VIRTUAL_HOST_FORWARDED_HEADER,
    VALID_CANNED_ACLS_BUCKET,
)
from localstack.services.s3.exceptions import InvalidRequest
from localstack.services.s3.models_v2 import S3Bucket
from localstack.utils.aws import arns, aws_stack
from localstack.utils.aws.arns import parse_arn
from localstack.utils.strings import checksum_crc32, checksum_crc32c, hash_sha1, hash_sha256
from localstack.utils.urls import localstack_host

checksum_keys = ["ChecksumSHA1", "ChecksumSHA256", "ChecksumCRC32", "ChecksumCRC32C"]

BUCKET_NAME_REGEX = (
    r"(?=^.{3,63}$)(?!^(\d+\.)+\d+$)"
    + r"(^(([a-z0-9]|[a-z0-9][a-z0-9\-]*[a-z0-9])\.)*([a-z0-9]|[a-z0-9][a-z0-9\-]*[a-z0-9])$)"
)

REGION_REGEX = r"[a-z]{2}-[a-z]+-[0-9]{1,}"
PORT_REGEX = r"(:[\d]{0,6})?"

S3_VIRTUAL_HOSTNAME_REGEX = (  # path based refs have at least valid bucket expression (separated by .) followed by .s3
    r"^(http(s)?://)?((?!s3\.)[^\./]+)\."  # the negative lookahead part is for considering buckets
    r"(((s3(-website)?\.({}\.)?)localhost(\.localstack\.cloud)?)|(localhost\.localstack\.cloud)|"
    r"(s3((-website)|(-external-1))?[\.-](dualstack\.)?"
    r"({}\.)?amazonaws\.com(.cn)?)){}(/[\w\-. ]*)*$"
).format(
    REGION_REGEX, REGION_REGEX, PORT_REGEX
)

PATTERN_UUID = re.compile(
    r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}"
)


def get_full_default_bucket_location(bucket_name):
    if config.HOSTNAME_EXTERNAL != config.LOCALHOST:
        host_definition = localstack_host(
            use_hostname_external=True, custom_port=config.get_edge_port_http()
        )
        return f"{config.get_protocol()}://{host_definition.host_and_port()}/{bucket_name}/"
    else:
        host_definition = localstack_host(use_localhost_cloud=True)
        return f"{config.get_protocol()}://{bucket_name}.s3.{host_definition.host_and_port()}/"


def get_object_checksum_for_algorithm(checksum_algorithm: str, data: bytes):
    match checksum_algorithm:
        case ChecksumAlgorithm.CRC32:
            return checksum_crc32(data)

        case ChecksumAlgorithm.CRC32C:
            return checksum_crc32c(data)

        case ChecksumAlgorithm.SHA1:
            return hash_sha1(data)

        case ChecksumAlgorithm.SHA256:
            return hash_sha256(data)

        case _:
            # TODO: check proper error? for now validated client side, need to check server response
            raise InvalidRequest("The value specified in the x-amz-trailer header is not supported")


def verify_checksum(checksum_algorithm: str, data: bytes, request: Dict):
    # TODO: you don't have to specify the checksum algorithm
    # you can use only the checksum-{algorithm-type} header
    # https://docs.aws.amazon.com/AmazonS3/latest/userguide/checking-object-integrity.html
    key = f"Checksum{checksum_algorithm.upper()}"
    # TODO: is there a message if the header is missing?
    checksum = request.get(key)
    calculated_checksum = get_object_checksum_for_algorithm(checksum_algorithm, data)

    if calculated_checksum != checksum:
        raise InvalidRequest(
            f"Value for x-amz-checksum-{checksum_algorithm.lower()} header is invalid."
        )


def get_s3_checksum(algorithm):
    match algorithm:
        case ChecksumAlgorithm.CRC32:
            return S3CRC32Checksum()

        case ChecksumAlgorithm.CRC32C:
            from botocore.httpchecksum import CrtCrc32cChecksum

            return CrtCrc32cChecksum()

        case ChecksumAlgorithm.SHA1:
            return hashlib.sha1(usedforsecurity=False)

        case ChecksumAlgorithm.SHA256:
            return hashlib.sha256(usedforsecurity=False)

        case _:
            # TODO: check proper error? for now validated client side, need to check server response
            raise InvalidRequest("The value specified in the x-amz-trailer header is not supported")


class S3CRC32Checksum:
    __slots__ = ["checksum"]

    def __init__(self):
        self.checksum = None

    def update(self, value: bytes):
        if self.checksum is None:
            self.checksum = zlib.crc32(value)
            return

        self.checksum = zlib.crc32(value, self.checksum)

    def digest(self) -> bytes:
        return self.checksum.to_bytes(4, "big")


def is_key_expired(key_object: Union[FakeKey, FakeDeleteMarker]) -> bool:
    if not key_object or isinstance(key_object, FakeDeleteMarker) or not key_object._expiry:
        return False
    return key_object._expiry <= datetime.datetime.now(key_object._expiry.tzinfo)


def is_bucket_name_valid(bucket_name: str) -> bool:
    """
    ref. https://docs.aws.amazon.com/AmazonS3/latest/userguide/bucketnamingrules.html
    """
    return True if re.match(BUCKET_NAME_REGEX, bucket_name) else False


def is_canned_acl_bucket_valid(canned_acl: str) -> bool:
    return canned_acl in VALID_CANNED_ACLS_BUCKET


def get_header_name(capitalized_field: str) -> str:
    headers_parts = re.split(r"([A-Z][a-z]+)", capitalized_field)
    return f"x-amz-{'-'.join([part.lower() for part in headers_parts if part])}"


def is_valid_canonical_id(canonical_id: str) -> bool:
    """
    Validate that the string is a hex string with 64 char
    """
    try:
        return int(canonical_id, 16) and len(canonical_id) == 64
    except ValueError:
        return False


def get_class_attrs_from_spec_class(spec_class: Type[str]):
    return {attr for attr in vars(spec_class) if not attr.startswith("__")}


def get_metadata_from_headers(headers: Headers) -> Metadata:
    metadata = Metadata()
    meta_regex = re.compile(r"^x-amz-meta-([a-zA-Z0-9\-_.]+)$", flags=re.IGNORECASE)

    for header in headers.keys():
        if isinstance(header, str):
            result = meta_regex.match(header)
            meta_key = None
            if result:
                # Check for extra metadata
                meta_key = result.group(0).lower()
            elif header.lower() in METADATA_SETTABLE_HEADERS:
                # Check for special metadata that doesn't start with x-amz-meta
                meta_key = header
            # TODO: test multiple values for headers
            if meta_key:
                metadata[meta_key] = (
                    headers[header][0] if type(headers[header]) == list else headers[header]
                )
    return metadata


def get_owner_for_account_id(account_id: str):
    return Owner(
        DisplayName="webfile",  # only in certain regions, see above
        ID="75aa57f09aa0c8caeab4f8c24e99d10f8e7faeebf76c078efc7c6caea54ba06a",
    )  # TODO: find a way for that? to depends on the account id used? it will depend on region too? check it


def forwarded_from_virtual_host_addressed_request(headers: Dict[str, str]) -> bool:
    """
    Determines if the request was forwarded from a v-host addressing style into a path one
    """
    # we can assume that the host header we are receiving here is actually the header we originally received
    # from the client (because the edge service is forwarding the request in memory)
    match = re.match(S3_VIRTUAL_HOSTNAME_REGEX, headers.get(S3_VIRTUAL_HOST_FORWARDED_HEADER, ""))

    # checks whether there is a bucket name. This is sort of hacky
    return True if match and match.group(3) else False


def get_bucket_from_moto(
    moto_backend: moto_s3_models.S3Backend, bucket: BucketName
) -> moto_s3_models.FakeBucket:
    # TODO: check authorization for buckets as well?
    try:
        return moto_backend.get_bucket(bucket_name=bucket)
    except MissingBucket:
        raise NoSuchBucket("The specified bucket does not exist", BucketName=bucket)


def get_key_from_moto_bucket(
    moto_bucket: FakeBucket,
    key: ObjectKey,
    version_id: str = None,
    raise_if_delete_marker_method: Literal["GET", "PUT"] = None,
) -> FakeKey | FakeDeleteMarker:
    # TODO: rework the delete marker handling
    # we basically need to re-implement moto `get_object` to account for FakeDeleteMarker
    if version_id is None:
        fake_key = moto_bucket.keys.get(key)
    else:
        for key_version in moto_bucket.keys.getlist(key, default=[]):
            if str(key_version.version_id) == str(version_id):
                fake_key = key_version
                break
        else:
            fake_key = None

    if not fake_key:
        raise NoSuchKey("The specified key does not exist.", Key=key)

    if isinstance(fake_key, FakeDeleteMarker) and raise_if_delete_marker_method:
        # TODO: validate method, but should be PUT in most cases (updating a DeleteMarker)
        match raise_if_delete_marker_method:
            case "GET":
                raise NoSuchKey("The specified key does not exist.", Key=key)
            case "PUT":
                raise MethodNotAllowed(
                    "The specified method is not allowed against this resource.",
                    Method="PUT",
                    ResourceType="DeleteMarker",
                )

    return fake_key


def get_bucket_and_key_from_s3_uri(s3_uri: str) -> Tuple[str, Optional[str]]:
    """
    Extracts the bucket name and key from s3 uri
    """
    output_bucket, _, output_key = s3_uri.removeprefix("s3://").partition("/")
    return output_bucket, output_key


def _create_invalid_argument_exc(
    message: Union[str, None], name: str, value: str, host_id: str = None
) -> InvalidArgument:
    ex = InvalidArgument(message)
    ex.ArgumentName = name
    ex.ArgumentValue = value
    if host_id:
        ex.HostId = host_id
    return ex


def capitalize_header_name_from_snake_case(header_name: str) -> str:
    return "-".join([part.capitalize() for part in header_name.split("-")])


def validate_kms_key_id(kms_key: str, bucket: FakeBucket | S3Bucket) -> None:
    """
    Validate that the KMS key used to encrypt the object is valid
    :param kms_key: the KMS key id or ARN
    :param bucket: the targeted bucket
    :raise KMS.DisabledException if the key is disabled
    :raise KMS.NotFoundException if the key is not in the same region or does not exist
    :return: the key ARN if found and enabled
    """
    if hasattr(bucket, "region_name"):
        bucket_region = bucket.region_name
    else:
        bucket_region = bucket.bucket_region

    try:
        parsed_arn = parse_arn(kms_key)
        key_region = parsed_arn["region"]
        # the KMS key should be in the same region as the bucket, we can raise an exception without calling KMS
        if key_region != bucket_region:
            raise CommonServiceException(
                code="KMS.NotFoundException", message=f"Invalid arn {key_region}"
            )

    except InvalidArnException:
        # if it fails, the passed ID is a UUID with no region data
        key_id = kms_key
        # recreate the ARN manually with the bucket region and bucket owner
        # if the KMS key is cross-account, user should provide an ARN and not a KeyId
        kms_key = arns.kms_key_arn(
            key_id=key_id, account_id=bucket.account_id, region_name=bucket_region
        )

    # the KMS key should be in the same region as the bucket, create the client in the bucket region
    kms_client = aws_stack.connect_to_service("kms", region_name=bucket_region)
    try:
        key = kms_client.describe_key(KeyId=kms_key)
        if not key["KeyMetadata"]["Enabled"]:
            if key["KeyMetadata"]["KeyState"] == "PendingDeletion":
                raise CommonServiceException(
                    code="KMS.KMSInvalidStateException",
                    message=f'{key["KeyMetadata"]["Arn"]} is pending deletion.',
                )
            raise CommonServiceException(
                code="KMS.DisabledException", message=f'{key["KeyMetadata"]["Arn"]} is disabled.'
            )

    except ClientError as e:
        if e.response["Error"]["Code"] == "NotFoundException":
            raise CommonServiceException(
                code="KMS.NotFoundException", message=e.response["Error"]["Message"]
            )
        raise
