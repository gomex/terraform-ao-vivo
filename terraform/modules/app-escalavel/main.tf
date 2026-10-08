locals {
  fqdn = "${var.subdominio}.${var.dominio}"
  azs  = slice(data.aws_availability_zones.disponiveis.names, 0, 2)
}

data "aws_availability_zones" "disponiveis" {
  state = "available"
}

data "aws_ami" "contador" {
  most_recent = true
  owners      = ["self"]

  filter {
    name   = "name"
    values = ["${var.nome}-*"]
  }
}

data "aws_route53_zone" "dominio" {
  name = var.dominio
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  name = var.nome
  cidr = "10.0.0.0/16"

  azs                     = local.azs
  public_subnets          = ["10.0.1.0/24", "10.0.2.0/24"]
  map_public_ip_on_launch = true
  enable_nat_gateway      = false
}

module "acm" {
  source  = "terraform-aws-modules/acm/aws"
  version = "~> 5.0"

  domain_name         = local.fqdn
  zone_id             = data.aws_route53_zone.dominio.zone_id
  validation_method   = "DNS"
  wait_for_validation = true
}

module "alb" {
  source  = "terraform-aws-modules/alb/aws"
  version = "~> 9.0"

  name                       = var.nome
  vpc_id                     = module.vpc.vpc_id
  subnets                    = module.vpc.public_subnets
  enable_deletion_protection = false

  security_group_ingress_rules = {
    http = {
      from_port   = 80
      to_port     = 80
      ip_protocol = "tcp"
      cidr_ipv4   = "0.0.0.0/0"
    }
    https = {
      from_port   = 443
      to_port     = 443
      ip_protocol = "tcp"
      cidr_ipv4   = "0.0.0.0/0"
    }
  }

  security_group_egress_rules = {
    vpc = {
      ip_protocol = "-1"
      cidr_ipv4   = module.vpc.vpc_cidr_block
    }
  }

  listeners = {
    http = {
      port     = 80
      protocol = "HTTP"
      redirect = {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }
    https = {
      port            = 443
      protocol        = "HTTPS"
      certificate_arn = module.acm.acm_certificate_arn
      forward = {
        target_group_key = "contador"
      }
    }
  }

  target_groups = {
    contador = {
      name_prefix          = "cont-"
      protocol             = "HTTP"
      port                 = 80
      target_type          = "instance"
      deregistration_delay = 10
      create_attachment    = false

      health_check = {
        enabled             = true
        path                = "/saude"
        matcher             = "200"
        interval            = 10
        timeout             = 5
        healthy_threshold   = 2
        unhealthy_threshold = 2
      }
    }
  }
}

module "sg_maquinas" {
  source  = "terraform-aws-modules/security-group/aws"
  version = "~> 5.0"

  name        = "${var.nome}-maquinas"
  description = "Libera HTTP apenas vindo do load balancer"
  vpc_id      = module.vpc.vpc_id

  computed_ingress_with_source_security_group_id = [{
    rule                     = "http-80-tcp"
    source_security_group_id = module.alb.security_group_id
  }]
  number_of_computed_ingress_with_source_security_group_id = 1

  egress_rules = ["all-all"]
}

module "asg" {
  source  = "terraform-aws-modules/autoscaling/aws"
  version = "~> 8.0"

  name = var.nome

  desired_capacity = var.quantidade_de_maquinas
  min_size         = var.minimo_de_maquinas
  max_size         = var.maximo_de_maquinas

  vpc_zone_identifier       = module.vpc.public_subnets
  health_check_type         = "ELB"
  health_check_grace_period = 60
  wait_for_capacity_timeout = 0

  traffic_source_attachments = {
    alb = {
      traffic_source_identifier = module.alb.target_groups["contador"].arn
      traffic_source_type       = "elbv2"
    }
  }

  launch_template_name   = var.nome
  update_default_version = true
  image_id               = data.aws_ami.contador.id
  instance_type          = var.tipo_instancia
  security_groups        = [module.sg_maquinas.security_group_id]
  enable_monitoring      = false

  metadata_options = {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  create_iam_instance_profile = true
  iam_role_name               = var.nome
  iam_role_policies = {
    AutoScalingReadOnly = "arn:aws:iam::aws:policy/AutoScalingReadOnlyAccess"
    EC2ReadOnly         = "arn:aws:iam::aws:policy/AmazonEC2ReadOnlyAccess"
    ACMReadOnly         = "arn:aws:iam::aws:policy/AWSCertificateManagerReadOnly"
    Route53ReadOnly     = "arn:aws:iam::aws:policy/AmazonRoute53ReadOnlyAccess"
    SSM                 = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
  }

  autoscaling_group_tags = {
    Route53Zona     = data.aws_route53_zone.dominio.zone_id
    Route53Registro = local.fqdn
  }

  instance_refresh = {
    strategy = "Rolling"
    preferences = {
      min_healthy_percentage = 50
      instance_warmup        = 30
    }
  }
}

module "dns" {
  source  = "terraform-aws-modules/route53/aws//modules/records"
  version = "~> 4.0"

  zone_id = data.aws_route53_zone.dominio.zone_id

  records = [{
    name = var.subdominio
    type = "A"
    alias = {
      name    = module.alb.dns_name
      zone_id = module.alb.zone_id
    }
  }]
}
