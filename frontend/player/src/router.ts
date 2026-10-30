import { createRouter, createWebHistory } from 'vue-router'
import { h } from 'vue'
const placeholder = { render: () => h('span') }
export const router = createRouter({ history:createWebHistory(), routes:[
  {path:'/',redirect:'/tickets'},
  ...['tickets','create','chat','hotspots'].map(name=>({path:`/${name}`,name,component:placeholder})),
  {path:'/:pathMatch(.*)*',redirect:'/tickets'}
]})
