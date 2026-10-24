output "queue_url" {
  value = aws_sqs_queue.requests.url
}

output "queue_arn" {
  value = aws_sqs_queue.requests.arn
}

output "queue_name" {
  value = aws_sqs_queue.requests.name
}

output "dlq_name" {
  value = aws_sqs_queue.dlq.name
}

output "dlq_url" {
  value = aws_sqs_queue.dlq.url
}

output "event_bus_name" {
  value = aws_cloudwatch_event_bus.quotes.name
}

output "event_bus_arn" {
  value = aws_cloudwatch_event_bus.quotes.arn
}

output "scheduler_group_name" {
  value = aws_scheduler_schedule_group.quotes.name
}

output "alerts_topic_arn" {
  value = aws_sns_topic.alerts.arn
}

output "events_log_group" {
  value = aws_cloudwatch_log_group.events.name
}
