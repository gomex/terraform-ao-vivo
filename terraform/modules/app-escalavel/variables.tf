variable "nome" {
  description = "Nome usado nos recursos. A AMI procurada é a mais recente que começa com \"<nome>-\"."
  type        = string
}

variable "dominio" {
  description = "Zona do Route53 que já existe na conta. Ex.: exemplo.com.br"
  type        = string
}

variable "subdominio" {
  description = "Subdomínio que vai apontar para o load balancer."
  type        = string
}

variable "quantidade_de_maquinas" {
  description = "Quantas máquinas o Auto Scaling Group deve manter rodando."
  type        = number
}

variable "minimo_de_maquinas" {
  type    = number
  default = 1
}

variable "maximo_de_maquinas" {
  type    = number
  default = 10
}

variable "tipo_instancia" {
  type    = string
  default = "t3.micro"
}
