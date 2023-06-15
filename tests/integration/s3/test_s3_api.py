import pytest
from botocore.exceptions import ClientError


class TestS3BucketCRUD:
    def test_create_bucket(self, s3_create_bucket):
        pass

    def test_delete_bucket_with_objects(self, s3_bucket, aws_client):
        pass

    def test_delete_versioned_bucket_with_objects(self):
        pass


class TestS3ObjectCRUD:
    def test_delete_object(self, s3_bucket, aws_client, snapshot):
        key_name = "test-delete"
        put_object = aws_client.s3.put_object(Bucket=s3_bucket, Key=key_name, Body="test-delete")
        snapshot.match("put-object", put_object)

        delete_object = aws_client.s3.delete_object(Bucket=s3_bucket, Key=key_name)
        snapshot.match("delete-object", delete_object)

        delete_object_2 = aws_client.s3.delete_object(Bucket=s3_bucket, Key=key_name)
        snapshot.match("delete-nonexistent-object", delete_object_2)

    def test_delete_object_versioned(self, s3_bucket, aws_client, snapshot):
        snapshot.add_transformer(snapshot.transform.s3_api())
        # enable versioning on the bucket
        aws_client.s3.put_bucket_versioning(
            Bucket=s3_bucket, VersioningConfiguration={"Status": "Enabled"}
        )

        key_name = "test-delete"
        put_object = aws_client.s3.put_object(Bucket=s3_bucket, Key=key_name, Body="test-delete")
        snapshot.match("put-object", put_object)
        object_version_id = put_object["VersionId"]

        # try deleting the last version of the object, it sets a DeleteMarker on top
        delete_object = aws_client.s3.delete_object(Bucket=s3_bucket, Key=key_name)
        snapshot.match("delete-object", delete_object)
        delete_marker_version_id = delete_object["VersionId"]

        # try GetObject without VersionId
        with pytest.raises(ClientError) as e:
            aws_client.s3.get_object(Bucket=s3_bucket, Key=key_name)
        snapshot.match("get-deleted-object", e.value.response)

        # Boto does not parse those headers in the exception, but they are present
        response_headers = e.value.response["ResponseMetadata"]["HTTPHeaders"]
        assert response_headers["x-amz-delete-marker"] == "true"
        assert response_headers["x-amz-version-id"] == delete_marker_version_id

        # try GetObject with VersionId
        get_object_with_version = aws_client.s3.get_object(
            Bucket=s3_bucket, Key=key_name, VersionId=object_version_id
        )
        snapshot.match("get-object-with-version", get_object_with_version)

        # try GetObject on a DeleteMarker
        with pytest.raises(ClientError) as e:
            aws_client.s3.get_object(
                Bucket=s3_bucket, Key=key_name, VersionId=delete_marker_version_id
            )
        snapshot.match("get-delete-marker", e.value.response)

        # Boto does not parse those headers in the exception, but they are present
        response_headers = e.value.response["ResponseMetadata"]["HTTPHeaders"]
        assert response_headers["x-amz-delete-marker"] == "true"
        assert response_headers["x-amz-version-id"] == delete_marker_version_id
        assert response_headers["allow"] == "DELETE"

        # delete again without specifying a VersionId, this will just pile another DeleteMarker onto the stack
        delete_object_2 = aws_client.s3.delete_object(Bucket=s3_bucket, Key=key_name)
        snapshot.match("delete-object-2", delete_object_2)

        list_object_version = aws_client.s3.list_object_versions(Bucket=s3_bucket, Prefix=key_name)
        snapshot.match("list-object-versions", list_object_version)

        # delete a DeleteMarker directly
        # try deleting the last version of the object, it sets a DeleteMarker on top
        delete_marker = aws_client.s3.delete_object(
            Bucket=s3_bucket, Key=key_name, VersionId=delete_marker_version_id
        )
        snapshot.match("delete-delete-marker", delete_marker)
        # assert that the returned VersionId is the same as the DeleteMarker, indicating that the DeleteMarker
        # was deleted
        assert delete_object["VersionId"] == delete_marker_version_id

        # delete the object directly, without setting a DeleteMarker
        delete_object_version = aws_client.s3.delete_object(
            Bucket=s3_bucket, Key=key_name, VersionId=object_version_id
        )
        snapshot.match("delete-object-version", delete_object_version)
        # assert that we properly deleted an object and did not set a DeleteMarker or deleted One
        assert "DeleteMarker" not in delete_object_version

        # try GetObject with VersionId on the now delete ObjectVersion
        with pytest.raises(ClientError) as e:
            aws_client.s3.get_object(Bucket=s3_bucket, Key=key_name, VersionId=object_version_id)
        snapshot.match("get-deleted-object-with-version", e.value.response)

        response_headers = e.value.response["ResponseMetadata"]["HTTPHeaders"]
        assert "x-amz-delete-marker" not in response_headers
        assert "x-amz-version-id" not in response_headers

        print(delete_marker_version_id, object_version_id)
        # try to delete with a wrong VersionId
        # TODO: VALIDATION OF VERSION ID
        with pytest.raises(ClientError) as e:
            aws_client.s3.delete_object(
                Bucket=s3_bucket,
                Key=key_name,
                VersionId=object_version_id[:-4] + "ABCD",
            )
        snapshot.match("delete-with-bad-version", e.value.response)

        response_headers = e.value.response["ResponseMetadata"]["HTTPHeaders"]
        assert "x-amz-delete-marker" not in response_headers
        assert "x-amz-version-id" not in response_headers

        # try deleting a never existing object
        delete_wrong_key = aws_client.s3.delete_object(Bucket=s3_bucket, Key="wrong-key")
        snapshot.match("delete-wrong-key", delete_wrong_key)

    def test_delete_object_locked(self):
        pass

    def test_delete_object_on_suspended_bucket(self):
        pass
