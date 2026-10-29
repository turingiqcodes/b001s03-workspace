terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

locals {
  # Tags the sandbox requires on every taggable resource (Name is added per resource)
  common_tags = {
    StudentID = var.student_id
    Purpose   = "terraform-exercise"
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = local.common_tags
  }
}
