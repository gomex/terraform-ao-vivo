output "url" {
  description = "Abra no navegador (ou projete na tela)."
  value       = "https://${local.fqdn}"
}

output "alb_dns" {
  value = module.alb.dns_name
}

output "ami" {
  value = "${data.aws_ami.contador.name} (${data.aws_ami.contador.id})"
}

output "asg" {
  value = module.asg.autoscaling_group_name
}
