terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.regiao

  default_tags {
    tags = {
      Projeto       = "terraform-ao-vivo"
      GerenciadoPor = "terraform"
    }
  }
}

variable "regiao" {
  type    = string
  default = "us-east-1"
}

variable "repositorio" {
  description = "Repositório do GitHub (dono/nome) que pode assumir a role."
  type        = string
  default     = "gomex/terraform-ao-vivo"
}

variable "repositorio_oidc" {
  description = "Prefixo do sub do token OIDC do GitHub (dono@id/repo@id)."
  type        = string
  default     = "gomex@95132/terraform-ao-vivo@1395417554"
}

variable "criar_oidc_provider" {
  description = "Use false se a conta já tiver o OIDC provider do GitHub (só pode existir um por conta)."
  type        = bool
  default     = true
}

module "state" {
  source  = "terraform-aws-modules/s3-bucket/aws"
  version = "~> 4.0"

  bucket_prefix = "terraform-ao-vivo-state-"

  versioning = {
    enabled = true
  }

  server_side_encryption_configuration = {
    rule = {
      apply_server_side_encryption_by_default = {
        sse_algorithm = "AES256"
      }
    }
  }

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

module "github_oidc_provider" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-github-oidc-provider"
  version = "~> 5.0"

  create = var.criar_oidc_provider
}

module "github_oidc_role" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-github-oidc-role"
  version = "~> 5.0"

  name = "terraform-ao-vivo-github"

  subjects = [
    "${var.repositorio_oidc}:ref:refs/heads/main",
    "${var.repositorio_oidc}:pull_request",
  ]

  policies = {
    Admin = "arn:aws:iam::aws:policy/AdministratorAccess"
  }

  depends_on = [module.github_oidc_provider]
}

module "github_oidc_role_plan" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-github-oidc-role"
  version = "~> 5.0"

  name     = "terraform-ao-vivo-github-plan"
  subjects = ["${var.repositorio_oidc}:environment:plan-fork"]

  policies = {
    ReadOnly = "arn:aws:iam::aws:policy/ReadOnlyAccess"
  }

  depends_on = [module.github_oidc_provider]
}

output "variaveis_do_github" {
  description = "Rode estes comandos (ou cadastre em Settings > Secrets and variables > Actions > Variables)."
  value       = <<-EOT
    gh variable set AWS_ROLE_ARN --repo ${var.repositorio} --body "${module.github_oidc_role.arn}"
    gh variable set AWS_PLAN_ROLE_ARN --repo ${var.repositorio} --body "${module.github_oidc_role_plan.arn}"
    gh variable set AWS_REGION --repo ${var.repositorio} --body "${var.regiao}"
    # coloque o bucket "${module.state.s3_bucket_id}" em terraform/versions.tf (backend "s3")
  EOT
}
