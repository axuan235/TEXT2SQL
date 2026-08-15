sql
-- ============================================================
-- 场景：近三个月与多名男性同住的年轻女性
-- 策略：在同一酒店+同一房间+日期有重叠，即为同住
-- 同住次数>=2次视为多次同住，与>=2名男性多次同住即为目标
-- ============================================================

WITH 
-- Step 1: 筛选近三个月的入住记录
recent_stays AS (
    SELECT 
        stay_id,
        guest_id,
        hotel_id,
        room_no,
        check_in_date,
        check_out_date
    FROM hotel_stays
    WHERE check_in_date >= DATE_SUB(CURRENT_DATE(), 90)  -- 近三个月
      AND check_in_date <= CURRENT_DATE()                -- 不包含未来日期
),

-- Step 2: 生成同住组合（同一酒店+同一房间+日期重叠）
co_stay_pairs AS (
    SELECT 
        a.guest_id AS guest_a,
        b.guest_id AS guest_b,
        a.hotel_id,
        a.room_no,
        a.check_in_date AS a_check_in,
        a.check_out_date AS a_check_out,
        b.check_in_date AS b_check_in,
        b.check_out_date AS b_check_out
    FROM recent_stays a
    INNER JOIN recent_stays b
        ON a.hotel_id = b.hotel_id
        AND a.room_no = b.room_no
        AND a.guest_id < b.guest_id  -- 避免重复组合（A,B）和（B,A）
        AND a.check_in_date < b.check_out_date  -- 日期重叠条件
        AND b.check_in_date < a.check_out_date
),

-- Step 3: 统计每对人员的同住次数
frequent_pairs AS (
    SELECT 
        guest_a,
        guest_b,
        COUNT(*) AS co_stay_count,
        COLLECT_SET(hotel_id) AS hotels,  -- 记录在哪些酒店同住过
        COLLECT_SET(room_no) AS rooms     -- 记录在哪些房间同住过
    FROM co_stay_pairs
    GROUP BY guest_a, guest_b
    HAVING COUNT(*) >= 2  -- 多次同住（>=2次）
),

-- Step 4: 关联人员信息，筛选年轻女性与男性的组合
target_pairs AS (
    SELECT 
        fp.guest_a,
        fp.guest_b,
        fp.co_stay_count,
        g1.name AS female_name,
        g1.age AS female_age,
        g2.name AS male_name,
        g2.age AS male_age
    FROM frequent_pairs fp
    INNER JOIN guests g1 
        ON fp.guest_a = g1.guest_id
        AND g1.gender = 'F' 
        AND g1.age BETWEEN 18 AND 35  -- 定义年轻女性（可根据业务调整）
    INNER JOIN guests g2 
        ON fp.guest_b = g2.guest_id
        AND g2.gender = 'M'
        AND g2.age >= 18              -- 成年男性
),

-- Step 5: 统计每个年轻女性与多少名男性多次同住
female_male_stats AS (
    SELECT 
        guest_a AS female_id,
        female_name,
        female_age,
        COUNT(DISTINCT guest_b) AS male_companion_count,  -- 不同男性人数
        COLLECT_LIST(
            STRUCT(guest_b AS male_id, male_name, male_age, co_stay_count)
        ) AS male_details
    FROM target_pairs
    GROUP BY guest_a, female_name, female_age
    HAVING COUNT(DISTINCT guest_b) >= 2  -- 与至少2名男性多次同住
)

-- Step 6: 最终结果输出
SELECT 
    female_id,   -- 用户ID 
    female_name, -- 用户姓名
    female_age,  -- 用户年龄
    male_companion_count, --用户 最近三个月 男性 同住人员个数
    male_details  -- 用户 最近三个月 男性 同住人员列表
FROM female_male_stats
ORDER BY male_companion_count DESC, female_age ASC;

