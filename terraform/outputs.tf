output "url" {
  description = "Abra no navegador (ou projete na tela)."
  value       = module.app.url
}

output "alb_dns" {
  value = module.app.alb_dns
}

output "ami" {
  value = module.app.ami
}

output "asg" {
  value = module.app.asg
}
