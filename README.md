# terraform-ao-vivo

Um repositório para ensinar Terraform ao vivo em uma palestra.

A ideia: subir um **Auto Scaling Group** atrás de um **Load Balancer** com um
domínio no **Route53**, e deixar a plateia abrir o site no celular. A página
mostra, em tempo real, **quantas máquinas estão rodando**. Quando você muda um
número no Terraform e faz o merge, o **GitHub Actions** roda o `apply` e todo
mundo vê as máquinas aparecendo (ou sumindo) na tela.

```
                 Route53 (demo.seudominio)
                          │
                    ACM (HTTPS)
                          │
              Application Load Balancer
                  │                │
            ┌─────┴──────┐  ┌──────┴─────┐
            │ EC2 (AZ a) │  │ EC2 (AZ b) │   ← Auto Scaling Group
            └────────────┘  └────────────┘
               AMI criada com Packer + Ansible
```

## O que tem aqui

| Caminho              | O que faz                                                                  |
|----------------------|----------------------------------------------------------------------------|
| `app/`               | O app "contador de máquinas" (Python, só stdlib + boto3)                   |
| `ansible/`           | Playbook que instala o app e cria o serviço no systemd                     |
| `packer/`            | Gera a AMI (Ubuntu 24.04) rodando o playbook do Ansible                    |
| `terraform/`         | A infra da demo: o `main.tf` só chama o módulo `app-escalavel`             |
| `terraform/modules/app-escalavel/` | Módulo que junta os módulos prontos do `terraform-aws-modules` |
| `bootstrap/`         | Roda uma vez: bucket do state + role OIDC para o GitHub Actions            |
| `.github/workflows/` | Pipelines de deploy (só actions oficiais)                                   |

Módulos usados:

- [`terraform-aws-modules/vpc/aws`](https://registry.terraform.io/modules/terraform-aws-modules/vpc/aws)
- [`terraform-aws-modules/acm/aws`](https://registry.terraform.io/modules/terraform-aws-modules/acm/aws)
- [`terraform-aws-modules/alb/aws`](https://registry.terraform.io/modules/terraform-aws-modules/alb/aws)
- [`terraform-aws-modules/security-group/aws`](https://registry.terraform.io/modules/terraform-aws-modules/security-group/aws)
- [`terraform-aws-modules/autoscaling/aws`](https://registry.terraform.io/modules/terraform-aws-modules/autoscaling/aws)
- [`terraform-aws-modules/route53/aws//modules/records`](https://registry.terraform.io/modules/terraform-aws-modules/route53/aws)
- No bootstrap: [`s3-bucket`](https://registry.terraform.io/modules/terraform-aws-modules/s3-bucket/aws) e [`iam`](https://registry.terraform.io/modules/terraform-aws-modules/iam/aws) (`iam-github-oidc-provider` e `iam-github-oidc-role`)

## Pipelines

Todas usam só actions oficiais: `actions/checkout`, `actions/github-script`,
`aws-actions/configure-aws-credentials`, `hashicorp/setup-terraform` e
`hashicorp/setup-packer`. A autenticação na AWS é via **OIDC**, sem access
key guardada em secret.

| Workflow           | Quando roda                                                    | O que faz                                                          |
|--------------------|----------------------------------------------------------------|--------------------------------------------------------------------|
| `pull-request.yml` | Todo PR                                                        | `terraform plan` comentado no PR (só PRs internos) + validação do Packer e do Ansible |
| `pull-request-fork.yml` | PR de fork, depois da sua aprovação                       | `terraform plan` com role só de leitura, comentado no PR            |
| `terraform.yml`    | Push na `main` mexendo em `terraform/`, ou manual              | `terraform apply`                                                  |
| `ami.yml`          | Push na `main` mexendo em `app/`, `ansible/` ou `packer/`, ou manual | `packer build` e depois chama o `terraform.yml` (instance refresh) |
| `destroy.yml`      | Manual, digitando `destruir`                                   | `terraform destroy`                                                |

A versão da AMI mostrada na tela é `v<número da execução>`, ou o valor que
você digitar ao rodar o workflow da AMI manualmente.

## Pré-requisitos

- Conta na AWS com uma **hosted zone pública no Route53** (ex.: `exemplo.com.br`)
- VPC default na região (o Packer usa ela para criar a máquina temporária)
- Localmente, só para o bootstrap: Terraform >= 1.10 e credenciais de admin da AWS

## Configuração (uma vez só)

1. **Bootstrap** — cria o bucket do state e a role que o GitHub assume:

   ```bash
   make bootstrap            # ou: make bootstrap REGIAO=sa-east-1
   ```

   Se a conta já tiver o OIDC provider do GitHub, use
   `terraform -chdir=bootstrap apply -var criar_oidc_provider=false`.
   O state do bootstrap fica local (`bootstrap/terraform.tfstate`), então guarde-o.

2. **Environment para PRs de fork** — em *Settings → Environments → New
   environment*, crie `plan-fork` e marque **Required reviewers** com o seu
   usuário. Em *Deployment branches and tags*, deixe *No restriction*, porque
   o evento dos PRs de fork roda a partir da `main`.
   **Faça isto antes de cadastrar as variáveis:** se o environment não
   existir, o GitHub cria um automaticamente, *sem revisor*, e o plan
   rodaria sem a sua aprovação.

3. **Variáveis do repositório** — o output `variaveis_do_github` já traz os
   comandos prontos do `gh`. Ou cadastre em *Settings → Secrets and variables →
   Actions → Variables*:

   | Variável          | Exemplo                                              |
   |-------------------|------------------------------------------------------|
   | `AWS_ROLE_ARN`    | `arn:aws:iam::123456789012:role/terraform-ao-vivo-github` |
   | `AWS_PLAN_ROLE_ARN` | `arn:aws:iam::123456789012:role/terraform-ao-vivo-github-plan` |
   | `AWS_REGION`      | `us-east-1`                                          |

4. **Configuração** — edite [`terraform/main.tf`](terraform/main.tf) com a
   sua zona do Route53 (`dominio`), o bucket do state em
   [`terraform/versions.tf`](terraform/versions.tf) e, se não for
   `us-east-1`, a região no provider.

5. **Primeiro deploy** — faça o push na `main`. O workflow **AMI** cria a
   imagem e em seguida faz o `apply` de toda a infra. Se quiser disparar na
   mão: *Actions → AMI → Run workflow*.

## Roteiro da demo

1. **A infra no ar** — mostre o `terraform/main.tf`: uma única chamada de
   módulo, com 12 linhas. Depois abra `terraform/modules/app-escalavel/main.tf`
   e mostre que por baixo ele só junta módulos prontos do registry, que
   criam dezenas de recursos. Abra a URL (está no
   resumo da execução do workflow) e projete: a página tem um QR Code para a
   plateia abrir no celular, e mostra o caminho da requisição com os recursos
   reais que o Terraform criou: Route53 → ACM → Load Balancer → Target Group →
   Auto Scaling Group → máquinas, tudo dentro da VPC.

2. **Escalar via Pull Request** — no próprio GitHub, edite
   `terraform/main.tf` mudando `quantidade_de_maquinas` (ex.: de 2
   para 6) e abra um PR. O workflow comenta o `plan` no PR: só **uma**
   mudança (`desired_capacity`). Faça o merge, e em ~1 minuto após o `apply`
   os cartões novos aparecem na tela.

3. **Load balancer em ação** — repare no aviso "Esta resposta veio da
   máquina...": a cada atualização uma máquina diferente responde.

4. **Auto-cura** — termine uma instância pelo console da AWS. O Auto Scaling
   Group percebe e sobe outra sozinho, sem nenhum `apply`.

5. **Nova versão (instance refresh)** — mude algo no app (ex.: uma cor em
   `app/contador.py`) e faça o push, ou rode *Actions → AMI → Run workflow*.
   O Packer gera a AMI nova, o Terraform atualiza o launch template e as
   máquinas são trocadas aos poucos, sem derrubar o site. O cartão do Auto
   Scaling Group mostra uma barra com o progresso da troca, e cada máquina
   mostra a versão do app e a AMI que está usando.

6. **Reduzir** — outro PR voltando `quantidade_de_maquinas` para 1.

7. **Destruir** — *Actions → Destroy → Run workflow*, digitando `destruir`.

## PRs da plateia

A página mostra um segundo QR Code ("mande um PR") e um link para o
repositório. Quem quiser pode fazer um fork e mandar um PR mudando o app
(`app/contador.py`) ou o número de máquinas (`terraform/main.tf`). Você faz o
merge ao vivo e todo mundo vê a mudança chegar.

Como funciona com PRs de fork:

1. O GitHub **não entrega credenciais da AWS** para workflows de forks no
   evento `pull_request`. Por isso, nesses PRs, o `terraform plan` do
   `pull-request.yml` aparece como *skipped*. A validação do Packer e do
   Ansible roda normalmente (para quem nunca contribuiu, clique em
   *Approve and run* no PR).
2. O `pull-request-fork.yml` roda o plan desses PRs, mas o job fica
   **esperando a sua aprovação** no environment `plan-fork`. No PR aparece
   *"Waiting for review"*: revise o diff e clique em *Review deployments →
   Approve*. Cada push novo no PR gera uma nova execução, que precisa de nova
   aprovação.
3. O plan usa uma **role só de leitura** (`AWS_PLAN_ROLE_ARN`) e roda sem
   lock (`-lock=false`). O resultado é comentado no PR por um job separado,
   que não executa código do PR.
4. Faça o merge. O `apply` roda na `main` com a role de admin, que só aceita
   a `main` e PRs internos.

**Antes de aprovar o plan**, procure no diff por `data "external"`,
providers novos ou módulos com `source` fora do registry: são formas de
executar código durante o plan. **Antes do merge**, lembre que o código
mergeado roda nas máquinas e na pipeline de admin. Desconfie de PRs que
mexem em `.github/`, `packer/`, `ansible/` ou `bootstrap/`.

O endereço do QR Code vem do repositório onde o workflow da AMI roda. No
build local (`make ami`), o padrão está na variável `repositorio` em
`packer/ami.pkr.hcl`.

## Rodando localmente (opcional)

O `Makefile` tem os mesmos passos para rodar da sua máquina (precisa de
Packer e Ansible instalados):

```bash
make ami VERSAO=v1
make init
make plan
make apply
```

## Limpeza

- O `destroy` não apaga as AMIs criadas pelo Packer. Para removê-las, cancele
  o registro (deregister) das AMIs `terraform-ao-vivo-*` e apague os snapshots
  associados no console do EC2.
- Depois disso, se quiser remover o bucket e a role:
  `terraform -chdir=bootstrap destroy` (esvazie o bucket antes).

## Segurança e custos

- A role do GitHub tem `AdministratorAccess`, mas só pode ser assumida por
  workflows deste repositório. É uma conta de demo; em produção, restrinja
  as permissões.
- Tudo roda em `t3.micro`, sem NAT Gateway. O que mais pesa é o load balancer
  (~US$ 0,02/hora). Lembre de rodar o **Destroy** depois da palestra.
