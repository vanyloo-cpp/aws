# SIS 3 · Orders API

Cloud Application Development · Week 11 · Maximov Samir

## How it works

1. A client calls `POST /orders` through an Application Load Balancer. The Python API runs in a Linux container on ECS Fargate. The ALB checks `GET /health`.
2. The API publishes `OrderCreated` to a dedicated EventBridge bus. The rule matches the event source and type and forwards the event to a standard SQS queue. HTTP `202` means EventBridge accepted the event for asynchronous processing.
3. Lambda reads the queue and stores the processing result in DynamoDB. The partition key is `orderId`. A conditional write with `attribute_not_exists(orderId)` makes checking for a duplicate and committing the result one atomic operation. A duplicate is acknowledged and logged.
4. For a processing error, Lambda returns the individual SQS message ID for retry. After five unsuccessful receives, the main queue redrives the message to its DLQ. That DLQ is for consumer failures after SQS receives the event.
5. The ECS execution role pulls the image and writes logs. The task role publishes only `sis3.orders` / `OrderCreated` to this stack's bus. The worker has its own role. Both log groups retain logs for seven days.

`infra/registry.yaml` creates an ECR repository with immutable tags and a lifecycle policy for untagged images. The tag is derived from the API build inputs. The deployment resolves it to an ECR digest and points ECS to that digest. `infra/application.yaml` creates the VPC, ALB, ECS, messaging, worker and DynamoDB resources. Each deployment gets an independent stack pair.

## Deploy from CloudShell

Open AWS CloudShell in **Europe (Frankfurt)** and use its normal public environment. It includes AWS CLI and Docker. The active AWS identity needs deployment permissions for the CloudFormation resources, ECR image upload and IAM roles.

Upload `SIS3_Orders_AWS.zip` with **Actions → Upload file**, then run:

```bash
unzip SIS3_Orders_AWS.zip
cd sis3-orders
AWS_REGION=eu-central-1 bash deploy.sh sis3-orders-dev dev
```

From the project root, the last line is the deployment command. It validates both templates, deploys the registry, builds and pushes the image if its tag is new, then deploys the application. Allow several minutes for the load balancer and ECS service to become healthy. The command prints the app's CloudFormation outputs and accepts an optional third argument such as `203.0.113.10/32` to limit HTTP to a real client IPv4 address or range. The default is public HTTP for a short lab demonstration. Use sample order IDs only.

The Fargate task gets a public IP for outbound traffic to ECR and AWS APIs. Its security group accepts inbound connections on port 8080 only from the ALB. The lab uses a single small task and no NAT gateway.

## Verify the first deployment

```bash
AWS_REGION=eu-central-1 bash demo.sh sis3-orders-dev
```

The script checks the ALB response, publishes two requests with the same `orderId`, then looks for `processed` and `duplicate` in CloudWatch and displays the DynamoDB item. Both successful POST requests return HTTP `202` because both publish separate events. Only the first DynamoDB write succeeds.

To demonstrate a failed message reaching the DLQ, use the `QueueUrl` and `DLQUrl` values printed by the deploy command:

```bash
export AWS_REGION=eu-central-1
QUEUE_URL='paste-QueueUrl-here'
DLQ_URL='paste-DLQUrl-here'
aws sqs send-message --queue-url "$QUEUE_URL" --message-body '{"demo":"invalid order"}'
```

After several minutes, check the DLQ message count:

```bash
aws sqs get-queue-attributes --queue-url "$DLQ_URL" \
  --attribute-names ApproximateNumberOfMessages
```

Capture CloudFormation stack status and outputs, the ECS service with a healthy task, the ECR tag and lifecycle rule, the queue's redrive policy, and the successful `demo.sh` output. The AWS Console can display these resources and their settings.

## Defence from a clean clone

The archive includes an initial Git commit. This command uses the extracted repository as the clone source. Use your Git remote URL in place of `.` if you have published the repository.

```bash
git clone . ../sis3-defence
cd ../sis3-defence
AWS_REGION=eu-central-1 bash deploy.sh sis3-orders-defence defence
AWS_REGION=eu-central-1 bash demo.sh sis3-orders-defence
```

Show both stack names in CloudFormation and the working API for the second stack. With independent stacks, the two demos use separate buses, queues, tables and repositories.

## Teardown

Delete each application stack before its registry stack. The templates delete demo data and ECR images during stack deletion. Use the exact stack name of the demo you deployed:

```bash
export AWS_REGION=eu-central-1
STACK=sis3-orders-defence
aws cloudformation delete-stack --stack-name "$STACK"
aws cloudformation wait stack-delete-complete --stack-name "$STACK"
aws cloudformation delete-stack --stack-name "$STACK-registry"
aws cloudformation wait stack-delete-complete --stack-name "$STACK-registry"
```

Repeat for `sis3-orders-dev` after capturing the evidence you need. ALB, Fargate tasks and public IPv4 addresses can incur charges while running.
