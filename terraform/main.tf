module "app" {
  source = "./modules/app-escalavel"

  nome       = "terraform-ao-vivo"
  dominio    = "mesa.gomex.me"
  subdominio = "aovivo"

  quantidade_de_maquinas = 2
  minimo_de_maquinas     = 1
  maximo_de_maquinas     = 10
  tipo_instancia         = "t3.micro"
}
