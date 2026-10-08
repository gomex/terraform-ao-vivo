terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    bucket       = "terraform-ao-vivo-state-aws-evento"
    key          = "terraform-ao-vivo.tfstate"
    use_lockfile = true
  }
}

provider "aws" {
  region = "us-east-1"

  default_tags {
    tags = {
      Projeto       = "terraform-ao-vivo"
      GerenciadoPor = "terraform"
    }
  }
}
