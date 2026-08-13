// ============================================================
// 音乐播放器配置文件
// ============================================================
// 本文件由 3.html 通过 <script src="./resource/music-config.js"> 引入
// 在 3.html 中通过以下三个变量读取当前曲目信息：
//   BGM_URL   = getCurrentTrack().url    → 传给 new Audio() 播放
//   BGM_COVER = getCurrentTrack().cover  → 传给 <img src> 作为封面
//   BGM_TITLE = getCurrentTrack().title  → 显示在卡片和全屏标题处
// ============================================================
// 添加新曲目：
//   在下方 PLAYLIST 数组中追加一个对象，包含 title / url / cover
//   然后将 currentTrackIndex 改为新曲目的索引即可
// 移除曲目：
//   从 PLAYLIST 数组中删除对应对象，并调整 currentTrackIndex
// ============================================================

const PLAYLIST = [
    {
        title: '永恒宁静的藏书塔',                        // 曲目标题
        url: './resource/audio/永恒宁静的藏书塔.mp3',     // 音频文件路径
        cover: './resource/images/cover.jpg'               // 封面图片路径
    }
    // 添加更多曲目示例（取消注释并填写实际路径）：
    // {
    //     title: '曲目名称',
    //     url: './resource/audio/xxx.mp3',
    //     cover: './resource/images/xxx.jpg'
    // }
];

// 当前播放曲目索引（默认第一首，索引从 0 开始）
let currentTrackIndex = 0;

// 获取当前播放曲目的完整信息，供 3.html 调用
function getCurrentTrack() {
    return PLAYLIST[currentTrackIndex];
}