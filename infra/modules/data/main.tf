# S3 buckets (catalog, generated PDFs, audit archive) and the single DynamoDB table.

locals {
  buckets = {
    catalog = "${var.name}-catalog-${var.account_id}"
    quotes  = "${var.name}-quotes-${var.account_id}"
    audit   = "${var.name}-audit-${var.account_id}"
  }
}

resource "aws_s3_bucket" "this" {
  for_each            = local.buckets
  bucket              = each.value
  force_destroy       = var.force_destroy
  object_lock_enabled = each.key == "audit" ? var.enable_object_lock : null
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each                = aws_s3_bucket.this
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_ownership_controls" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

# Catalog and audit are versioned (catalog history; object lock requires versioning)
resource "aws_s3_bucket_versioning" "catalog" {
  bucket = aws_s3_bucket.this["catalog"].id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_versioning" "audit" {
  bucket = aws_s3_bucket.this["audit"].id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_object_lock_configuration" "audit" {
  count  = var.enable_object_lock ? 1 : 0
  bucket = aws_s3_bucket.this["audit"].id
  rule {
    default_retention {
      mode = "GOVERNANCE"
      days = var.audit_retention_days
    }
  }
  depends_on = [aws_s3_bucket_versioning.audit]
}

resource "aws_s3_bucket_lifecycle_configuration" "quotes" {
  bucket = aws_s3_bucket.this["quotes"].id
  rule {
    id     = "expire-pdfs"
    status = "Enabled"
    filter { prefix = "quotes/" }
    expiration { days = var.pdf_retention_days }
  }
  rule {
    id     = "abort-multipart"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload { days_after_initiation = 1 }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "catalog" {
  bucket = aws_s3_bucket.this["catalog"].id
  rule {
    id     = "old-catalog-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration { noncurrent_days = 365 }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "audit" {
  bucket = aws_s3_bucket.this["audit"].id
  rule {
    id     = "audit-to-infrequent-access"
    status = "Enabled"
    filter {}
    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }
  }
}

# Deny non-TLS access on every bucket
data "aws_iam_policy_document" "tls_only" {
  for_each = aws_s3_bucket.this
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    resources = [each.value.arn, "${each.value.arn}/*"]
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "this" {
  for_each   = aws_s3_bucket.this
  bucket     = each.value.id
  policy     = data.aws_iam_policy_document.tls_only[each.key].json
  depends_on = [aws_s3_bucket_public_access_block.this]
}

# The catalog: uploaded under a versioned key and as "current.json" (what the app reads)
resource "aws_s3_object" "catalog_current" {
  bucket       = aws_s3_bucket.this["catalog"].id
  key          = "catalog/current.json"
  source       = var.catalog_file
  etag         = filemd5(var.catalog_file)
  content_type = "application/json"
}

resource "aws_s3_object" "catalog_versioned" {
  bucket       = aws_s3_bucket.this["catalog"].id
  key          = "catalog/${basename(var.catalog_file)}"
  source       = var.catalog_file
  etag         = filemd5(var.catalog_file)
  content_type = "application/json"
}

# ---------------------------------------------------------------- DynamoDB
resource "aws_dynamodb_table" "quotes" {
  name                        = "${var.name}-quotes"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "PK"
  range_key                   = "SK"
  stream_enabled              = true
  stream_view_type            = "NEW_IMAGE"
  deletion_protection_enabled = var.deletion_protection

  attribute {
    name = "PK"
    type = "S"
  }
  attribute {
    name = "SK"
    type = "S"
  }
  attribute {
    name = "GSI1PK"
    type = "S"
  }
  attribute {
    name = "GSI1SK"
    type = "S"
  }

  global_secondary_index {
    name            = "GSI1"
    hash_key        = "GSI1PK"
    range_key       = "GSI1SK"
    projection_type = "ALL"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = var.enable_pitr
  }

  server_side_encryption {
    enabled = true # AWS-owned key is free; switch to a CMK if your policy requires it
  }
}
