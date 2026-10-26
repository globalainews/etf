const api=require('../../utils/api');
Page({
 data:{isDemo:api.isDemo,loading:true,submitting:false,error:'',user:null,nickname:'',videoMemberName:''},
 onShow(){this.load();},
 async load(){this.setData({loading:true,error:''});try{const user=await api.call('user.status');this.setData({user,nickname:user.nickname||'',videoMemberName:user.videoMemberName||''});}catch(e){this.setData({error:e.message||'加载会员状态失败'});}finally{this.setData({loading:false});}},
 inputNickname(e){this.setData({nickname:e.detail.value.slice(0,32)});},
 inputVideoName(e){this.setData({videoMemberName:e.detail.value.slice(0,40)});},
 async submit(){if(this.data.submitting)return;const nickname=this.data.nickname.trim();const videoMemberName=this.data.videoMemberName.trim();if(!nickname||!videoMemberName){this.setData({error:'请填写微信昵称和视频号会员名称。'});return;}this.setData({submitting:true,error:''});try{const user=await api.call('member.apply',{nickname,videoMemberName});this.setData({user});wx.showToast({title:user.message||'申请已提交',icon:'none'});}catch(e){this.setData({error:e.message||'提交失败，请稍后重试'});}finally{this.setData({submitting:false});}},
 close(){const pages=getCurrentPages();if(pages.length>1)wx.navigateBack();else wx.switchTab({url:'/pages/profile/profile'});},
 continue(){wx.switchTab({url:'/pages/index/index'});}
});

