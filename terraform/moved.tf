moved {
  from = module.vpc
  to   = module.app.module.vpc
}

moved {
  from = module.acm
  to   = module.app.module.acm
}

moved {
  from = module.alb
  to   = module.app.module.alb
}

moved {
  from = module.sg_maquinas
  to   = module.app.module.sg_maquinas
}

moved {
  from = module.asg
  to   = module.app.module.asg
}

moved {
  from = module.dns
  to   = module.app.module.dns
}
