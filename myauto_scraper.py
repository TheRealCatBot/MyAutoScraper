import asyncio
import aiohttp
import os
import zipfile
import argparse
import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

load_dotenv()

def get_s3_client():
    return boto3.client(
        "s3",
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name=os.getenv("AWS_REGION"),
    )

# ─────────────────────────────────────────────
# API helpers
# ─────────────────────────────────────────────

def build_page_url(page_n: int) -> str:
    return (
        f"https://api2.myauto.ge/ka/products"
        f"?TypeID=0&ForRent=&Mans=&CurrencyID=3&MileageType=1&Page={page_n}"
    )

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/58.0.3029.110 Safari/537.3"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# ─────────────────────────────────────────────
# Image download
# ─────────────────────────────────────────────

async def download_image(session: aiohttp.ClientSession, url: str, save_directory: str) -> None:
    try:
        async with session.get(url) as response:
            response.raise_for_status()
            filename = os.path.basename(url)
            save_path = os.path.join(save_directory, filename)
            with open(save_path, "wb") as f:
                async for chunk in response.content.iter_chunked(1024):
                    f.write(chunk)
            print(f"  ✔ Downloaded: {filename}")
    except aiohttp.ClientError as e:
        print(f"  ✘ Failed to download {url}: {e}")


async def collect_image_urls(session: aiohttp.ClientSession, pages: int) -> list[str]:
    """Iterate through listing pages and collect all full-size image URLs."""
    urls: list[str] = []
    for page_n in range(1, pages + 1):
        print(f"[Page {page_n}] Fetching listing …")
        async with session.get(build_page_url(page_n)) as resp:
            resp.raise_for_status()
            data = await resp.json()
        items = data.get("data", {}).get("items", [])
        for item in items:
            car_id = item["car_id"]
            photo   = item["photo"]
            pic_num = item["pic_number"]
            for idx in range(1, pic_num + 1):
                urls.append(
                    f"https://static.my.ge/myauto/photos/{photo}/large/{car_id}_{idx}.jpg"
                )
        print(f"  → {len(items)} cars found on page {page_n}")
    return urls


async def download_all_images(urls: list[str], save_directory: str, concurrency: int = 20) -> None:
    os.makedirs(save_directory, exist_ok=True)
    semaphore = asyncio.Semaphore(concurrency)

    async def bounded_download(session: aiohttp.ClientSession, url: str) -> None:
        async with semaphore:
            await download_image(session, url, save_directory)

    async with aiohttp.ClientSession() as session:
        await asyncio.gather(*[bounded_download(session, url) for url in urls])

# ─────────────────────────────────────────────
# Zip helper
# ─────────────────────────────────────────────

def zip_directory(directory: str) -> str:
    zip_path = f"{directory}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(directory):
            for file in files:
                full_path = os.path.join(root, file)
                zf.write(full_path, arcname=file)
    size_mb = os.path.getsize(zip_path) / (1024 * 1024)
    print(f"ZIP created: {zip_path}  ({size_mb:.2f} MB)")
    return zip_path

# ─────────────────────────────────────────────
# S3 upload
# ─────────────────────────────────────────────

def upload_directory_to_s3(local_directory: str, bucket_name: str, s3_prefix: str = "") -> None:
    """Recursively upload every file under *local_directory* to S3."""
    s3 = get_s3_client()
    uploaded = 0
    for root, _, files in os.walk(local_directory):
        for filename in files:
            local_path = os.path.join(root, filename)
            relative   = os.path.relpath(local_path, local_directory)
            s3_key     = os.path.join(s3_prefix, relative).replace("\\", "/")
            try:
                s3.upload_file(local_path, bucket_name, s3_key)
                print(f"  ✔ Uploaded → s3://{bucket_name}/{s3_key}")
                uploaded += 1
            except ClientError as exc:
                print(f"  ✘ Failed to upload {local_path}: {exc}")
    print(f"\nTotal files uploaded to S3: {uploaded}")


def upload_file_to_s3(local_path: str, bucket_name: str, s3_key: str) -> None:
    """Upload a single file to S3."""
    s3 = get_s3_client()
    try:
        s3.upload_file(local_path, bucket_name, s3_key)
        print(f"  ✔ Uploaded ZIP → s3://{bucket_name}/{s3_key}")
    except ClientError as exc:
        print(f"  ✘ Failed to upload ZIP: {exc}")

# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

async def main(args: argparse.Namespace) -> None:
    # ── 1. Scrape image URLs ──────────────────
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        image_urls = await collect_image_urls(session, args.pages)

    print(f"\nTotal images to download: {len(image_urls)}\n")

    # ── 2. Download images ────────────────────
    await download_all_images(image_urls, args.output_dir, concurrency=args.concurrency)

    total = sum(len(files) for _, _, files in os.walk(args.output_dir))
    print(f"\nDownloaded {total} images to '{args.output_dir}/'")

    # ── 3. Optional: zip ─────────────────────
    zip_path = None
    if args.zip:
        zip_path = zip_directory(args.output_dir)

    # ── 4. Optional: S3 upload ────────────────
    if args.s3_bucket:
        print(f"\nUploading to S3 bucket '{args.s3_bucket}' …")
        if args.zip and zip_path:
            # upload the single zip file
            s3_key = os.path.join(args.s3_prefix, os.path.basename(zip_path)).replace("\\", "/")
            upload_file_to_s3(zip_path, args.s3_bucket, s3_key)
        else:
            # upload every image individually (triggers per-image Lambda events)
            upload_directory_to_s3(args.output_dir, args.s3_bucket, args.s3_prefix)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Download car images from myauto.ge and upload them to AWS S3."
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=1,
        metavar="N",
        help="Number of listing pages to scrape (default: 1, ~40 cars/page).",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="downloaded_images",
        metavar="DIR",
        help="Local directory to save downloaded images (default: downloaded_images).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=20,
        metavar="N",
        help="Maximum simultaneous download connections (default: 20).",
    )
    parser.add_argument(
        "--zip",
        action="store_true",
        help="Compress downloaded images into a ZIP archive after downloading.",
    )
    parser.add_argument(
        "--s3-bucket",
        type=str,
        default=os.getenv("S3_BUCKET"),
        metavar="BUCKET",
        help="S3 bucket name (defaults to S3_BUCKET in .env).",
    )
    parser.add_argument(
        "--s3-prefix",
        type=str,
        default=os.getenv("S3_PREFIX", "myauto-images"),
        metavar="PREFIX",
        help="S3 key prefix / folder path.",
    )

    asyncio.run(main(parser.parse_args()))
