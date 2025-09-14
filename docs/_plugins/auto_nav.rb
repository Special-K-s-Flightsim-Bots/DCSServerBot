# _plugins/auto_nav.rb
# ------------------------------------------------------------------
# Just‑the‑Docs auto‑navigation generator
#
# Scans the site’s root (excluding _site, _config.yml, _data, _posts, etc.)
# and builds a `navigation.yml` file in `_data/` that matches the folder
# structure and uses the file title (first H1) or the filename as fallback.
# ------------------------------------------------------------------
module AutoNav
  class Generator < Jekyll::Generator
    safe true
    priority :low

    # Called by Jekyll
    def generate(site)
      nav = []

      Dir.glob('**/*', File::FNM_DOTMATCH).each do |path|
        next if skip_path?(path)

        # Convert Windows backslashes to forward slashes
        path = path.tr('\\', '/')
        next unless File.extname(path) == '.md'

        # Build a relative URL
        url = File.basename(path, '.md')
        url = '/' if url == 'index'
        url = File.join('/', File.dirname(path), url, '.html')
        url = url.gsub('//', '/')

        # Grab the first H1 or fallback to filename
        title = extract_title(path) || File.basename(path, '.md').capitalize

        # Build a hierarchical structure
        add_to_nav(nav, path, title, url)
      end

      # Write the navigation file
      File.open('_data/navigation.yml', 'w') { |f| f.write(nav.to_yaml) }

      Jekyll.logger.info "AutoNav:", "Generated navigation.yml with #{nav.size} top‑level items."
    end

    private

    def skip_path?(path)
      # Skip hidden files, Jekyll defaults, and directories that start with _
      File.directory?(path) || path.start_with?('_') || path =~ /\A\.{1,2}\z/
    end

    # Return the first <h1> text from the markdown file
    def extract_title(md_path)
      content = File.read(md_path)
      # simple regex: line starting with # followed by space
      line = content.lines.find { |l| l.start_with?('# ') }
      line ? line.sub(/^# /, '').strip : nil
    end

    # Insert item into the nav hierarchy
    def add_to_nav(nav, path, title, url)
      parts = path.split('/').reject { |p| p.start_with?('_') }
      parts.pop   # drop .md extension
      parts.map! { |p| p.sub(/\.md$/, '') }
      current = nav

      parts.each_with_index do |part, idx|
        item = current.find { |i| i['title'] == part }
        if item.nil?
          # new node
          item = { 'title' => part }
          item['sub_navigation'] = [] if idx < parts.size - 1
          current << item
        end
        current = item['sub_navigation'] if idx < parts.size - 1
      end

      # Final leaf
      leaf = { 'title' => title, 'url' => url }
      current << leaf
    end
  end
end
