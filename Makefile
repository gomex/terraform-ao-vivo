REGIAO ?= us-east-1
VERSAO ?= v1

.PHONY: bootstrap ami init plan apply destroy

## Uma vez só: cria o bucket do state e a role OIDC do GitHub Actions
bootstrap:
	terraform -chdir=bootstrap init
	terraform -chdir=bootstrap apply 

## O resto normalmente roda no GitHub Actions; estes alvos são para rodar local.
ami:
	packer init packer
	packer build  -var versao_app=$(VERSAO) packer

init:
	terraform -chdir=terraform init -backend-config="region=$(REGIAO)"

plan:
	terraform -chdir=terraform plan 

apply:
	terraform -chdir=terraform apply 

destroy:
	terraform -chdir=terraform destroy 
