import json
import os
import urllib.parse
import uuid

import boto3
from botocore.exceptions import ClientError

rekognition = boto3.client("rekognition")
dynamodb = boto3.resource("dynamodb")

TABLE_NAME = os.environ.get("DYNAMO_DB_TABLE", "rekogintionAnalysesDB")


def detect_image_labels(bucket, key, max_labels=10):
    return rekognition.detect_labels(
        Image={"S3Object": {"Bucket": bucket, "Name": key}},
        MaxLabels=max_labels,
    )


def start_video_label_detection(bucket, key):
    resp = rekognition.start_label_detection(
        Video={"S3Object": {"Bucket": bucket, "Name": key}},
        NotificationChannel={
            "SNSTopicArn": os.environ["REKOGNITION_SNS_TOPIC_ARN"],
            "RoleArn": os.environ["REKOGNITION_ROLE_ARN"],
        },
    )
    return resp["JobId"]


def get_video_labels(job_id):
    resp = rekognition.get_label_detection(JobId=job_id)
    next_token = resp.get("NextToken")
    while next_token:
        page = rekognition.get_label_detection(JobId=job_id, NextToken=next_token)
        resp["Labels"].extend(page["Labels"])
        next_token = page.get("NextToken")
    return resp


def _make_dynamo_safe(data):
    if isinstance(data, dict):
        return {k: _make_dynamo_safe(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_make_dynamo_safe(v) for v in data]
    if isinstance(data, float):
        return str(data)
    return data


def save_labels_to_dynamodb(data, media_name, media_bucket):
    data.pop("ResponseMetadata", None)
    if "JobStatus" in data:
        del data["JobStatus"]
        data["mediaType"] = "Video"
    else:
        data["mediaType"] = "Image"
    item_id = str(uuid.uuid4())
    data.update({"id": item_id, "mediaName": media_name, "mediaBucket": media_bucket})
    table = dynamodb.Table(TABLE_NAME)
    table.put_item(Item=_make_dynamo_safe(data))
    return item_id


def start_processing_media(event, context):
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        ext = key.rsplit(".", 1)[-1].lower()
        if ext in ("jpeg", "jpg", "png"):
            result = detect_image_labels(bucket, key)
            save_labels_to_dynamodb(result, key, bucket)
        elif ext == "mp4":
            start_video_label_detection(bucket, key)


def handle_label_detection(event, context):
    for record in event.get("Records", []):
        message = json.loads(record["Sns"]["Message"])
        job_id = message["JobId"]
        s3_key = message["Video"]["S3ObjectName"]
        s3_bucket = message["Video"]["S3Bucket"]
        result = get_video_labels(job_id)
        save_labels_to_dynamodb(result, s3_key, s3_bucket)
