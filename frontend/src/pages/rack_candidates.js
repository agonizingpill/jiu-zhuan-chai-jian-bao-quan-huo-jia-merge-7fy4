        import {workspace} from '../workspace.js';
        import {el} from '../dom.js';

        const page = {
  "title": "孔形读数逐批压缩",
  "module": "货架分簇候选",
  "caption": "货架分簇候选",
  "detail": "BIRCH 增量学习只使用同用途和未合垛库存。返潮隔离独立由写入规则确认。",
  "path": "/rack_candidates",
  "columns": [
    {
      "name": "code",
      "label": "编号"
    },
    {
      "name": "member",
      "label": "试配砖垛"
    },
    {
      "name": "pair",
      "label": "同组砖垛"
    },
    {
      "name": "cluster_size",
      "label": "同簇总垛数"
    },
    {
      "name": "token",
      "label": "计算号"
    },
    {
      "name": "status",
      "label": "状态"
    },
    {
      "name": "distance",
      "label": "孔形距离"
    }
  ],
  "layout": "split",
  "view": "grid",
  "actions": [
    {
      "name": "arrange_pairs",
      "label": "首排配对",
      "path": "/rack_candidates/arrange-pairs",
      "fields": [],
      "help": "取已放行、复测且湿度达标、未入架的砖垛，按用途和孔形簇生成每垛一次、每架不超过八十块的最优配对。",
      "roles": [
        "operator",
        "reviewer"
      ],
      "success": "首排完成"
    },
    {
      "name": "rearrange_pairs",
      "label": "重排配对",
      "path": "/rack_candidates/rearrange-pairs",
      "fields": [],
      "help": "沿用首排孔形簇，尽量保留旧配对；已签货架卡不动，被替换的旧候选不能再签架。",
      "roles": [
        "operator",
        "reviewer"
      ],
      "success": "重排完成"
    },
    {
      "name": "suggest_pair",
      "label": "计算同簇砖垛",
      "path": "/rack_candidates/suggest-pair",
      "fields": [
        {
          "name": "stack",
          "label": "试配砖垛号",
          "kind": "text",
          "initial": "BR-X"
        }
      ],
      "help": "输入砖垛号，从历史孔形中寻找候选。",
      "roles": [
        "operator",
        "reviewer"
      ],
      "success": "已写入"
    },
    {
      "name": "record_candidate",
      "label": "保留货架合垛候选",
      "path": "/rack_candidates/record-candidate",
      "fields": [
        {
          "name": "code",
          "label": "候选号",
          "kind": "text",
          "initial": "COMP-24"
        },
        {
          "name": "token",
          "label": "计算号",
          "kind": "token",
          "initial": ""
        }
      ],
      "help": "候选始终附上两个砖垛号。",
      "roles": [
        "operator",
        "reviewer"
      ],
      "success": "已写入"
    }
  ]
};

        export const {title} = page;
        export const key = "rack_candidates";
        export async function render(root, role) {
          await workspace(root, role, page);

        }
