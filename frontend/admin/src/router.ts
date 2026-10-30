import { createRouter, createWebHistory } from 'vue-router'
import { h } from 'vue'
const placeholder = { render: () => h('span') }
export const router = createRouter({ history:createWebHistory(), routes:[
  {path:'/',redirect:'/dashboard'},
  ...['dashboard','pool','mine','all','knowledge','hotspots','users','audit'].map(name=>({path:`/${name}`,name,component:placeholder})),
  {path:'/:pathMatch(.*)*',redirect:'/dashboard'}
]})
