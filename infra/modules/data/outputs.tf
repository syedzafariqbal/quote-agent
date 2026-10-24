output "catalog_bucket" {
  value = aws_s3_bucket.this["catalog"].bucket
}

output "catalog_bucket_arn" {
  value = aws_s3_bucket.this["catalog"].arn
}

output "catalog_key" {
  value = aws_s3_object.catalog_current.key
}

output "quotes_bucket" {
  value = aws_s3_bucket.this["quotes"].bucket
}

output "quotes_bucket_arn" {
  value = aws_s3_bucket.this["quotes"].arn
}

output "audit_bucket" {
  value = aws_s3_bucket.this["audit"].bucket
}

output "audit_bucket_arn" {
  value = aws_s3_bucket.this["audit"].arn
}

output "table_name" {
  value = aws_dynamodb_table.quotes.name
}

output "table_arn" {
  value = aws_dynamodb_table.quotes.arn
}

output "table_stream_arn" {
  value = aws_dynamodb_table.quotes.stream_arn
}
