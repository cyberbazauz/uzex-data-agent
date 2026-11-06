#!/usr/bin/env bash
# Read-only AWS inventory for the UZEX Data Agent V2.1 deployment.
#
# This script NEVER creates, modifies or deletes anything. It only calls
# describe-*, list-* and get-caller-identity. Run it before choosing a
# topology and record its output in docs/deployment_report.md.
#
# Required:
#   AWS_PROFILE or an approved credential source
#   AWS_REGION (or pass the region as the first argument)
#
# Usage:
#   export AWS_PROFILE=<approved-profile>
#   bash deploy/aws/inventory.sh [region]
set -euo pipefail

REGION="${1:-${AWS_REGION:-}}"
if [[ -z "$REGION" ]]; then
  echo "ERROR: set AWS_REGION or pass a region: bash deploy/aws/inventory.sh us-east-1" >&2
  exit 1
fi

if ! command -v aws >/dev/null 2>&1; then
  echo "ERROR: AWS CLI is not installed (aws --version failed)." >&2
  echo "Install it, configure an approved profile, then rerun this script." >&2
  exit 1
fi

echo "== Identity (read-only) =="
aws sts get-caller-identity --output json
echo

echo "== Account aliases =="
aws iam list-account-aliases --output json || true
echo

echo "== Region =="
echo "$REGION"
echo

echo "== VPCs =="
aws ec2 describe-vpcs --region "$REGION" \
  --query 'Vpcs[].{VpcId:VpcId,Cidr:CidrBlock,Default:IsDefault,State:State}' \
  --output table
echo

echo "== Subnets =="
aws ec2 describe-subnets --region "$REGION" \
  --query 'Subnets[].{SubnetId:SubnetId,VpcId:VpcId,Az:AvailabilityZone,Cidr:CidrBlock,PublicIpOnLaunch:MapPublicIpOnLaunch,Name:Tags[?Key==`Name`]|[0].Value}' \
  --output table
echo

echo "== Internet gateways =="
aws ec2 describe-internet-gateways --region "$REGION" \
  --query 'InternetGateways[].{IgwId:InternetGatewayId,Attachments:Attachments[].VpcId}' \
  --output table
echo

echo "== Route tables =="
aws ec2 describe-route-tables --region "$REGION" \
  --query 'RouteTables[].{RouteTableId:RouteTableId,VpcId:VpcId,Routes:Routes[].{Dest:DestinationCidrBlock,Gw:GatewayId,State:State}}' \
  --output json || true
echo

echo "== Security groups (look for 0.0.0.0/0 on 22, 5432, 8000) =="
aws ec2 describe-security-groups --region "$REGION" \
  --query 'SecurityGroups[].{GroupId:GroupId,GroupName:GroupName,VpcId:VpcId,Ingress:IpPermissions[].{IpProtocol:IpProtocol,FromPort:FromPort,ToPort:ToPort,Cidrs:IpRanges[].CidrIp}}' \
  --output json
echo

echo "== EC2 instances =="
aws ec2 describe-instances --region "$REGION" \
  --query 'Reservations[].Instances[].{InstanceId:InstanceId,State:State.Name,Type:InstanceType,PublicIp:PublicIpAddress,PrivateIp:PrivateIpAddress,Profile:IamInstanceProfile.Arn,Name:Tags[?Key==`Name`]|[0].Value}' \
  --output table
echo

echo "== Existing ALBs =="
aws elbv2 describe-load-balancers --region "$REGION" \
  --query 'LoadBalancers[].{Name:LoadBalancerName,Dns:DNSName,Scheme:Scheme,VpcId:VpcId,State:State.Code}' \
  --output table 2>/dev/null || true
echo

echo "== ACM certificates =="
aws acm list-certificates --region "$REGION" \
  --query 'CertificateSummaryList[].{Arn:CertificateArn,Domain:DomainName,Status:Status}' \
  --output table 2>/dev/null || true
echo

echo "== RDS instances =="
aws rds describe-db-instances --region "$REGION" \
  --query 'DBInstances[].{Id:DBInstanceIdentifier,Engine:Engine,Class:DBInstanceClass,Status:DBInstanceStatus,Public:PubliclyAccessible,Endpoint:Endpoint.Address}' \
  --output table 2>/dev/null || true
echo

echo "== SSM parameters under /uzex/prod/ (names and types only, never values) =="
aws ssm describe-parameters --region "$REGION" \
  --parameter-filters "Key=Name,Option=BeginsWith,Values=/uzex/prod/" \
  --query 'Parameters[].{Name:Name,Type:Type,LastModified:LastModifiedDate}' \
  --output table 2>/dev/null || true
echo

echo "== IAM instance profiles =="
aws iam list-instance-profiles --output table 2>/dev/null || true
echo

echo "== S3 buckets (names only) =="
aws s3api list-buckets --query 'Buckets[].{Name:Name,Created:CreationDate}' --output table 2>/dev/null || true
echo

echo "== CloudFormation stacks =="
aws cloudformation list-stacks --region "$REGION" \
  --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE ROLLBACK_COMPLETE \
  --query 'StackSummaries[].{Name:StackName,Status:StackStatus,Created:CreationTime}' \
  --output table 2>/dev/null || true
echo

echo "Inventory complete. This run made no changes."
