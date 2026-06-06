# MyAuto Rekognition Pipeline

Scrapes car images from [myauto.ge](https://www.myauto.ge), uploads them to AWS S3, and automatically analyzes each image with AWS Rekognition. Results are stored in DynamoDB.

## Architecture

![Architecture Diagram](architecture.svg)

## How it works

1. Run the CLI scraper locally — it fetches car listings from myauto.ge and uploads the images to S3
2. Each S3 upload triggers a Lambda function automatically
3. Lambda sends the image to AWS Rekognition for label detection
4. Rekognition results are saved to a DynamoDB table (`rekogintionAnalysesDB`)

## Project structure

```
MyAutoScraper/
├── serverless.yml       # Infrastructure as code — deploys all AWS resources
├── lambda_handler.py    # AWS Lambda functions (Rekognition + DynamoDB)
├── myauto_scraper.py    # CLI script — scrapes myauto.ge and uploads to S3
├── architecture.svg     # Service architecture diagram
└── README.md
```

## Prerequisites

- Python 3.11+
- Node.js (for Serverless Framework)
- AWS account with credentials configured
- `aiohttp` and `boto3` Python packages

```bash
pip install aiohttp boto3
npm install -g serverless
```

## Deploy AWS infrastructure

Configure your AWS credentials first:

```bash
serverless config credentials --provider aws --key YOUR_KEY --secret YOUR_SECRET
```

Then deploy everything (S3 bucket, Lambda, DynamoDB, SNS, IAM roles) in one command:

```bash
serverless deploy
```

## Run the scraper

```bash
python myauto_scraper.py --pages 2 --s3-bucket myauto-car-images-us-east-1
```

### CLI options

| Flag | Default | Description |
|------|---------|-------------|
| `--pages N` | `1` | Number of listing pages to scrape (~40 cars/page) |
| `--output-dir DIR` | `downloaded_images` | Local folder for downloaded images |
| `--concurrency N` | `20` | Max simultaneous download connections |
| `--zip` | off | Compress images into a ZIP after downloading |
| `--s3-bucket BUCKET` | — | S3 bucket to upload images to |
| `--s3-prefix PREFIX` | `myauto-images` | S3 folder path |

## Lambda functions

| Function | Trigger | Description |
|----------|---------|-------------|
| `start_processing_media` | S3 PutObject | Runs Rekognition label detection on each uploaded image |
| `handle_label_detection` | SNS | Handles async Rekognition results for video files |

### Environment variables

| Variable | Description |
|----------|-------------|
| `DYNAMO_DB_TABLE` | DynamoDB table name (default: `rekogintionAnalysesDB`) |
| `REKOGNITION_SNS_TOPIC_ARN` | SNS topic ARN for async video job callbacks |
| `REKOGNITION_ROLE_ARN` | IAM role ARN that Rekognition can assume |

## Tear down

To delete all AWS resources and avoid charges:

```bash
serverless remove
```
