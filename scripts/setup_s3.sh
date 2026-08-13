#!/bin/bash
# Create public S3 bucket phx-wrf-forecast in us-east-1 with CORS + 14-day lifecycle.
set -euo pipefail
export AWS_EC2_METADATA_DISABLED=true
BUCKET=phx-wrf-forecast
REGION=us-east-1

if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  echo "bucket exists: $BUCKET"
else
  aws s3api create-bucket --bucket "$BUCKET" --region "$REGION"
  echo "created $BUCKET"
fi

aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
  'BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=false,RestrictPublicBuckets=false'

aws s3api put-bucket-ownership-controls --bucket "$BUCKET" --ownership-controls \
  'Rules=[{ObjectOwnership=BucketOwnerEnforced}]' || true

POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "PublicReadLatestAndRuns",
      "Effect": "Allow",
      "Principal": "*",
      "Action": "s3:GetObject",
      "Resource": [
        "arn:aws:s3:::${BUCKET}/latest.json",
        "arn:aws:s3:::${BUCKET}/runs/*"
      ]
    }
  ]
}
EOF
)
aws s3api put-bucket-policy --bucket "$BUCKET" --policy "$POLICY"

CORS=$(cat <<EOF
{
  "CORSRules": [
    {
      "AllowedHeaders": ["*"],
      "AllowedMethods": ["GET", "HEAD"],
      "AllowedOrigins": [
        "https://kenny150r.github.io",
        "http://localhost:8000",
        "http://127.0.0.1:8000"
      ],
      "ExposeHeaders": ["ETag", "Content-Length"],
      "MaxAgeSeconds": 3000
    }
  ]
}
EOF
)
aws s3api put-bucket-cors --bucket "$BUCKET" --cors-configuration "$CORS"

LIFE=$(cat <<EOF
{
  "Rules": [
    {
      "ID": "ExpireRuns14Days",
      "Status": "Enabled",
      "Filter": { "Prefix": "runs/" },
      "Expiration": { "Days": 14 }
    }
  ]
}
EOF
)
aws s3api put-bucket-lifecycle-configuration --bucket "$BUCKET" --lifecycle-configuration "$LIFE"

PLACE=$(mktemp)
echo '{"status":"awaiting-first-run","bucket":"phx-wrf-forecast"}' > "$PLACE"
aws s3 cp "$PLACE" "s3://$BUCKET/latest.json" --content-type application/json --cache-control 'public, max-age=10, must-revalidate'
rm -f "$PLACE"
echo "S3 bucket $BUCKET ready (public GetObject on latest.json + runs/*, CORS, 14-day lifecycle)"
