packer {
  required_plugins {
    amazon = {
      version = ">= 1.3.0"
      source  = "github.com/hashicorp/amazon"
    }
    ansible = {
      version = ">= 1.1.0"
      source  = "github.com/hashicorp/ansible"
    }
  }
}

variable "regiao" {
  type    = string
  default = "us-east-1"
}

variable "versao_app" {
  type        = string
  default     = "v1"
  description = "Versão exibida na tela do app. Troque para mostrar o instance refresh."
}

variable "repositorio" {
  type        = string
  default     = "https://github.com/gomex/terraform-ao-vivo"
  description = "Endereço do repositório, mostrado como QR Code na página para a plateia mandar PR."
}

variable "tipo_instancia" {
  type    = string
  default = "t3.micro"
}

locals {
  timestamp = regex_replace(timestamp(), "[- TZ:]", "")
}

source "amazon-ebs" "contador" {
  region        = var.regiao
  instance_type = var.tipo_instancia
  ssh_username  = "ubuntu"
  ami_name      = "terraform-ao-vivo-${var.versao_app}-${local.timestamp}"

  source_ami_filter {
    filters = {
      name                = "ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"
      root-device-type    = "ebs"
      virtualization-type = "hvm"
    }
    owners      = ["099720109477"]
    most_recent = true
  }

  tags = {
    Name    = "terraform-ao-vivo"
    Projeto = "terraform-ao-vivo"
    Versao  = var.versao_app
  }
}

build {
  sources = ["source.amazon-ebs.contador"]

  provisioner "ansible" {
    playbook_file    = "${path.root}/../ansible/playbook.yml"
    user             = "ubuntu"
    use_proxy        = false
    extra_arguments  = ["--extra-vars", "versao_app=${var.versao_app} repositorio=${var.repositorio}"]
    ansible_env_vars = ["ANSIBLE_HOST_KEY_CHECKING=False"]
  }
}
