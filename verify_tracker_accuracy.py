#!/usr/bin/env python3
# verify_tracker_accuracy.py
# 验证追踪器数据准确性 - 重新获取排名并与数据库记录比对

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import tracker
import database
from datetime import datetime

def verify_latest_data():
    """验证最新数据的准确性"""
    print("=" * 80)
    print("iOS排名追踪器数据准确性验证")
    print("=" * 80)
    print()
    
    # 获取数据库中的最新记录
    print("【步骤1】读取数据库中最新的排名记录...")
    latest = database.get_latest_rankings()
    
    if not latest:
        print("  ✗ 数据库中没有排名记录")
        return False
    
    print(f"  ✓ 找到 {len(latest)} 个游戏的最新记录")
    print()
    
    # 获取游戏ID到游戏名称的映射
    games = database.get_all_games()
    game_id_to_name = {g['id']: g['name'] for g in games}
    
    print(f"  ✓ 获取到 {len(games)} 个游戏的名称映射")
    print()
    
    # 重新获取当前排名（不保存到数据库）
    print("【步骤2】重新获取当前排名（使用MZStore API，不保存）...")
    current = tracker.fetch_rankings_for_verification()
    
    if not current:
        print("  ✗ 无法获取当前排名")
        return False
    
    print(f"  ✓ 成功获取 {len(current)} 个游戏的当前排名")
    print()
    
    # 比对数据
    print("【步骤3】比对数据库记录与当前获取的数据...")
    print()
    
    mismatches = []
    matches = []
    
    for game_id, regions in latest.items():
        # 获取游戏名称
        if game_id not in game_id_to_name:
            print(f"  ⚠️  游戏ID '{game_id}' 的名称未找到")
            continue
        
        game_name = game_id_to_name[game_id]
        
        if game_name not in current:
            print(f"  ⚠️  游戏 '{game_name}' 在当前获取的数据中未找到")
            continue
        
        for region, charts in regions.items():
            if region not in current[game_name]:
                print(f"  ⚠️  地区 '{region}' 在当前获取的数据中未找到")
                continue
            
            for chart_type, db_rank in charts.items():
                if chart_type not in current[game_name][region]:
                    print(f"  ⚠️  榜单 '{chart_type}' 在当前获取的数据中未找到")
                    continue
                
                current_rank = current[game_name][region][chart_type]
                
                # 比对
                if db_rank == current_rank:
                    matches.append({
                        'game': game_name,
                        'region': region,
                        'chart': chart_type,
                        'rank': db_rank,
                        'status': '一致'
                    })
                else:
                    mismatches.append({
                        'game': game_name,
                        'region': region,
                        'chart': chart_type,
                        'db_rank': db_rank,
                        'current_rank': current_rank,
                        'status': '不一致'
                    })
    
    # 输出比对结果
    print("=" * 80)
    print("比对结果")
    print("=" * 80)
    print()
    
    if matches:
        print(f"✓ 一致的数据: {len(matches)} 条")
        print()
        for m in matches[:10]:  # 只显示前10条
            rank_str = f"#{m['rank']}" if m['rank'] > 0 else "未上榜"
            print(f"  {m['game']} | {m['region'].upper()} | {m['chart']} | {rank_str}")
        if len(matches) > 10:
            print(f"  ... 还有 {len(matches) - 10} 条一致记录")
        print()
    
    if mismatches:
        print(f"✗ 不一致的数据: {len(mismatches)} 条")
        print()
        for m in mismatches:
            db_str = f"#{m['db_rank']}" if m['db_rank'] > 0 else "未上榜"
            cur_str = f"#{m['current_rank']}" if m['current_rank'] > 0 else "未上榜"
            print(f"  {m['game']} | {m['region'].upper()} | {m['chart']}")
            print(f"    DB: {db_str} vs 当前: {cur_str}")
        print()
        
        # 分析不一致的原因
        print("【分析】不一致的可能原因:")
        print("  1. 排名在两次查询之间发生了变化（正常波动）")
        print("  2. MZStore API返回了不同的数据（API波动）")
        print("  3. 数据库记录错误")
        print("  4. 地区配置或chart title映射错误")
        print()
    else:
        print("✓ 所有数据一致！追踪器工作正常。")
        print()
    
    # 生成验证报告
    print("=" * 80)
    print("验证报告摘要")
    print("=" * 80)
    print(f"验证时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"数据库记录数: {len(latest)} 个游戏")
    print(f"一致记录数: {len(matches)}")
    print(f"不一致记录数: {len(mismatches)}")
    
    total = len(matches) + len(mismatches)
    if total > 0:
        accuracy = len(matches) / total * 100
        print(f"数据一致性: {accuracy:.1f}%")
    else:
        print("数据一致性: N/A (无有效数据)")
    
    print()
    
    # 保存详细报告到文件
    report_file = f"验证报告_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write("=" * 80 + "\n")
        f.write("iOS排名追踪器数据准确性验证报告\n")
        f.write("=" * 80 + "\n\n")
        f.write(f"验证时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"数据库记录数: {len(latest)} 个游戏\n")
        f.write(f"一致记录数: {len(matches)}\n")
        f.write(f"不一致记录数: {len(mismatches)}\n")
        
        total = len(matches) + len(mismatches)
        if total > 0:
            accuracy = len(matches) / total * 100
            f.write(f"数据一致性: {accuracy:.1f}%\n")
        
        f.write("\n" + "=" * 80 + "\n")
        f.write("详细比对结果\n")
        f.write("=" * 80 + "\n\n")
        
        if matches:
            f.write(f"✓ 一致的数据 ({len(matches)} 条):\n")
            for m in matches:
                rank_str = f"#{m['rank']}" if m['rank'] > 0 else "未上榜"
                f.write(f"  {m['game']} | {m['region'].upper()} | {m['chart']} | {rank_str}\n")
            f.write("\n")
        
        if mismatches:
            f.write(f"✗ 不一致的数据 ({len(mismatches)} 条):\n")
            for m in mismatches:
                db_str = f"#{m['db_rank']}" if m['db_rank'] > 0 else "未上榜"
                cur_str = f"#{m['current_rank']}" if m['current_rank'] > 0 else "未上榜"
                f.write(f"  {m['game']} | {m['region'].upper()} | {m['chart']}\n")
                f.write(f"    DB: {db_str} vs 当前: {cur_str}\n")
            f.write("\n")
    
    print(f"详细报告已保存到: {report_file}")
    print()
    
    return len(mismatches) == 0

if __name__ == '__main__':
    success = verify_latest_data()
    sys.exit(0 if success else 1)
