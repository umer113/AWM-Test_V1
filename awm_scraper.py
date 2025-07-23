import requests
from bs4 import BeautifulSoup
import time
import json
import os
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from collections import deque
import pandas as pd

class AWMScraper:
    def __init__(self, base_url="https://www.awm.gov.au", max_workers=3, delay=2):
        self.base_url = base_url
        self.max_workers = max_workers
        self.delay = delay
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })
        
        self.visited_urls = set()
        self.to_visit = deque()
        self.scraped_data = []
        self.lock = threading.Lock()
        
        # Create output directory
        os.makedirs('output', exist_ok=True)
    
    def is_valid_url(self, url):
        """Check if URL belongs to AWM domain and is scrapable"""
        parsed = urlparse(url)
        return (parsed.netloc == 'www.awm.gov.au' and 
                not url.endswith(('.pdf', '.jpg', '.jpeg', '.png', '.gif', '.zip', '.doc', '.docx')))
    
    def extract_links(self, soup, current_url):
        """Extract all internal links from page"""
        links = set()
        for link in soup.find_all('a', href=True):
            href = link['href']
            absolute_url = urljoin(current_url, href)
            
            if self.is_valid_url(absolute_url):
                links.add(absolute_url)
        return links
    
    def scrape_page(self, url):
        """Scrape individual page and extract data"""
        try:
            with self.lock:
                if url in self.visited_urls:
                    return None
                self.visited_urls.add(url)
            
            print(f"Scraping: {url}")
            response = self.session.get(url, timeout=15)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Extract page data
            page_data = {
                'url': url,
                'title': soup.find('title').text.strip() if soup.find('title') else '',
                'content': self.extract_content(soup),
                'metadata': self.extract_metadata(soup)
            }
            
            # Find new links
            new_links = self.extract_links(soup, url)
            with self.lock:
                for link in new_links:
                    if link not in self.visited_urls:
                        self.to_visit.append(link)
            
            return page_data
            
        except Exception as e:
            print(f"Error scraping {url}: {str(e)}")
            return None
    
    def extract_content(self, soup):
        """Extract main content from page"""
        content = {}
        
        # Remove script and style elements
        for script in soup(["script", "style"]):
            script.decompose()
        
        # Extract main content areas
        main_content = soup.find('main') or soup.find('div', class_='content') or soup.body
        if main_content:
            content['main_text'] = main_content.get_text(strip=True, separator=' ')
        
        # Extract specific sections
        content['headings'] = [h.get_text(strip=True) for h in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])]
        content['paragraphs'] = [p.get_text(strip=True) for p in soup.find_all('p') if p.get_text(strip=True)]
        content['lists'] = [li.get_text(strip=True) for li in soup.find_all('li') if li.get_text(strip=True)]
        
        return content
    
    def extract_metadata(self, soup):
        """Extract metadata from page"""
        metadata = {}
        
        # Meta tags
        for meta in soup.find_all('meta'):
            name = meta.get('name') or meta.get('property')
            content = meta.get('content')
            if name and content:
                metadata[name] = content
        
        # Images
        metadata['images'] = [img.get('src') for img in soup.find_all('img') if img.get('src')]
        
        return metadata
    
    def save_data(self, data, filename):
        """Save scraped data to JSON file"""
        with open(f'output/{filename}', 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def flatten_data_for_excel(self, data):
        """Flatten nested data structure for Excel export"""
        flattened = []
        for item in data:
            flat_item = {
                'url': item.get('url', ''),
                'title': item.get('title', ''),
                'main_text': item.get('content', {}).get('main_text', ''),
                'headings_count': len(item.get('content', {}).get('headings', [])),
                'paragraphs_count': len(item.get('content', {}).get('paragraphs', [])),
                'lists_count': len(item.get('content', {}).get('lists', [])),
                'images_count': len(item.get('metadata', {}).get('images', [])),
                'description': item.get('metadata', {}).get('description', ''),
                'keywords': item.get('metadata', {}).get('keywords', ''),
            }
            flattened.append(flat_item)
        return flattened
    
    def scrape_website(self):
        """Main scraping function to scrape entire website"""
        self.to_visit.append(self.base_url)
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            while self.to_visit:
                # Get batch of URLs to process
                batch = []
                batch_size = min(self.max_workers, len(self.to_visit))
                
                for _ in range(batch_size):
                    if self.to_visit:
                        batch.append(self.to_visit.popleft())
                
                if not batch:
                    break
                
                # Submit tasks
                future_to_url = {executor.submit(self.scrape_page, url): url for url in batch}
                
                # Process results
                for future in as_completed(future_to_url):
                    result = future.result()
                    if result:
                        self.scraped_data.append(result)
                        
                        # Save incrementally every 50 pages
                        if len(self.scraped_data) % 50 == 0:
                            self.save_data(self.scraped_data, f'awm_data_batch_{len(self.scraped_data)}.json')
                            print(f"Progress: {len(self.scraped_data)} pages scraped, {len(self.to_visit)} URLs remaining")
                
                time.sleep(self.delay)
        
        # Final saves
        self.save_data(self.scraped_data, 'awm_complete_data.json')
        
        # Create Excel file
        flattened_data = self.flatten_data_for_excel(self.scraped_data)
        df = pd.DataFrame(flattened_data)
        excel_path = 'output/awm_scraped_data.xlsx'
        df.to_excel(excel_path, index=False, engine='openpyxl')
        
        print(f"Scraping complete! Total pages: {len(self.scraped_data)}")
        print(f"Excel file saved: {excel_path}")
        return self.scraped_data

# Main execution
if __name__ == "__main__":
    print("Starting comprehensive AWM website scraping...")
    scraper = AWMScraper(max_workers=3, delay=2)
    
    data = scraper.scrape_website()
    
    print(f"Scraping completed. {len(data)} pages scraped.")
    print("Files saved in 'output' directory:")
