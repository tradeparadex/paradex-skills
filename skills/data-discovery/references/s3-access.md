# S3 Access — IRSA Credential Bootstrap for DuckDB

The agent stack runs on EKS with IRSA (IAM Roles for Service Accounts). To
query `s3://terminal-dime-prod` from DuckDB, exchange the projected web
identity token for temporary STS credentials, then load them into DuckDB's
`httpfs` extension.

## Bootstrap (run once per session)

```bash
TOKEN=$(cat $AWS_WEB_IDENTITY_TOKEN_FILE)
CREDS=$(curl -s "https://sts.ap-northeast-1.amazonaws.com/?Action=AssumeRoleWithWebIdentity&Version=2011-06-15&RoleArn=${AWS_ROLE_ARN}&RoleSessionName=duckdb&WebIdentityToken=${TOKEN}")
AK=$(echo $CREDS | grep -o '<AccessKeyId>[^<]*' | cut -d'>' -f2)
SK=$(echo $CREDS | grep -o '<SecretAccessKey>[^<]*' | cut -d'>' -f2)
ST=$(echo $CREDS | grep -o '<SessionToken>[^<]*' | cut -d'>' -f2)
```

Required env vars:

- `AWS_WEB_IDENTITY_TOKEN_FILE` — projected service account token path.
- `AWS_ROLE_ARN` — IAM role to assume.

## Pass into DuckDB

```sql
INSTALL httpfs;
LOAD httpfs;
SET s3_region='ap-northeast-1';
SET s3_access_key_id='<AK>';
SET s3_secret_access_key='<SK>';
SET s3_session_token='<ST>';
```

## Token lifecycle

- STS tokens expire after **~1 hour**.
- On HTTP 400 `InvalidToken`, re-run the bootstrap to refresh.
- Treat refreshes as idempotent; safe to re-run mid-session.

## Verifying access

After bootstrap, the cheapest reachability check is a glob on a known-dense
prefix:

```sql
SELECT COUNT(*) FROM glob('s3://terminal-dime-prod/external/tardis/v1/quotes/combo/deribit/2026/05/**');
```

A non-zero count confirms credentials and network path are good.

## Coverage probe pattern

For any partitioned dataset (paths containing `YYYY/MM/DD`):

```sql
SELECT
  MIN(regexp_extract(file, '/(\d{4}/\d{2}/\d{2})/', 1)) AS earliest,
  MAX(regexp_extract(file, '/(\d{4}/\d{2}/\d{2})/', 1)) AS latest,
  COUNT(*) AS file_count
FROM glob('<s3-path-with-**>');
```

Use this before concluding "no data" for a recent date — the catalog's
verified ranges are point-in-time and the bucket grows forward.
