from app import app, db
from sqlalchemy import text

with app.app_context():
    with db.engine.connect() as conn:
        try:
            # Fix NULL item_description
            conn.execute(text('''
                UPDATE supplier_item 
                SET item_description = product_type 
                WHERE item_description IS NULL
            '''))
            
            # Fix NULL total_weight
            conn.execute(text('''
                UPDATE supplier_item 
                SET total_weight = 0 
                WHERE total_weight IS NULL
            '''))
            
            # Fix NULL total_bags
            conn.execute(text('''
                UPDATE supplier_item 
                SET total_bags = 0 
                WHERE total_bags IS NULL
            '''))
            
            conn.commit()
            print("✅ Fixed NULL values in supplier_item table!")
        except Exception as e:
            print(f"❌ Error: {e}")